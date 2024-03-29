from gae.initializations import *
import tensorflow as tf

flags = tf.app.flags
FLAGS = flags.FLAGS

# global unique layer ID dictionary for layer name assignment
_LAYER_UIDS = {}

def get_layer_uid(layer_name=''):
    """Helper function, assigns unique layer IDs
    """
    if layer_name not in _LAYER_UIDS:
        _LAYER_UIDS[layer_name] = 1
        return 1
    else:
        _LAYER_UIDS[layer_name] += 1
        return _LAYER_UIDS[layer_name]

def dropout_sparse(x, keep_prob, num_nonzero_elems):
    """Dropout for sparse tensors. Currently fails for very large sparse tensors (>1M elements)
    """
    noise_shape = [num_nonzero_elems]
    random_tensor = keep_prob
    random_tensor += tf.random_uniform(noise_shape)
    dropout_mask = tf.cast(tf.floor(random_tensor), dtype=tf.bool)
    pre_out = tf.sparse_retain(x, dropout_mask)
    return pre_out * (1./keep_prob)

def zeros(shape, name=None):
    """All zeros."""
    initial = tf.zeros(shape, dtype=tf.float32)
    return tf.Variable(initial, name=name)

class Layer(object):
    """Base layer class. Defines basic API for all layer objects.

    # Properties
        name: String, defines the variable scope of the layer.

    # Methods
        _call(inputs): Defines computation graph of layer
            (i.e. takes input, returns output)
        __call__(inputs): Wrapper for _call()
    """
    def __init__(self, **kwargs):
        allowed_kwargs = {'name', 'logging'}
        for kwarg in kwargs.keys():
            assert kwarg in allowed_kwargs, 'Invalid keyword argument: ' + kwarg
        name = kwargs.get('name')
        if not name:
            layer = self.__class__.__name__.lower()
            name = layer + '_' + str(get_layer_uid(layer))
        self.name = name
        self.vars = {}
        logging = kwargs.get('logging', False)
        self.logging = logging
        self.issparse = False

    def _call(self, inputs):
        return inputs

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            outputs = self._call(inputs)
            return outputs

class Dense(Layer):
    """Dense layer."""
    def __init__(self, input_dim, output_dim, dropout=0., pos=False, sparse_inputs=False, features_nonzero = 0,
                 act=tf.nn.relu, bias=False, featureless=False, **kwargs):
        super(Dense, self).__init__(**kwargs)

        self.dropout = dropout
        self.act = act
        self.sparse_inputs = sparse_inputs
        self.featureless = featureless
        self.bias = bias
        self.features_nonzero = features_nonzero

        with tf.variable_scope(self.name + '_vars'):
            self.vars['weights'] = weight_variable_glorot(input_dim, output_dim, name='weights')
            if pos:
                self.vars['weights'] = tf.square(self.vars['weights'])
            if self.bias:
                self.vars['bias'] = zeros([output_dim], name='bias')

        if self.logging:
            self._log_vars()

    def _call(self, inputs):
        x = inputs

        if self.sparse_inputs:
            x = dropout_sparse(x, 1-self.dropout, self.features_nonzero)
            output = tf.sparse_tensor_dense_matmul(x, self.vars['weights'])
        else:
            x = tf.nn.dropout(x, 1-self.dropout)
            output = tf.matmul(x, self.vars['weights'])

        # bias
        if self.bias:
            output += self.vars['bias']

        return self.act(output)

class GraphConvolutionDense(Layer):
    """Basic graph convolution layer for undirected graph without edge labels."""
    def __init__(self, input_dim, output_dim, sparse_inputs = False, features_nonzero=-1, dropout=0., act=tf.nn.relu, **kwargs):
        super(GraphConvolutionDense, self).__init__(**kwargs)
        with tf.variable_scope(self.name + '_vars'):
            self.vars['weights'] = weight_variable_glorot(input_dim, output_dim, name="weights")
        self.dropout = dropout
        self.act = act
        self.sparse_inputs = sparse_inputs
        self.features_nonzero = features_nonzero

    def _call(self, inputs):
        x = inputs[0]
        recon_1 = inputs[1]
        recon_2 = inputs[2]
        if self.sparse_inputs:
            x = dropout_sparse(x, 1-self.dropout, self.features_nonzero)
            x = tf.sparse_tensor_dense_matmul(x, self.vars['weights'])
        else:
            x = tf.nn.dropout(x, 1-self.dropout)
            x = tf.matmul(x, self.vars['weights'])
        x = tf.matmul(recon_1, tf.matmul(tf.transpose(recon_1), x)) + tf.matmul(recon_2, tf.matmul(tf.transpose(recon_2), x))
        outputs = self.act(x)
        return outputs

class GraphConvolution(Layer):
    """Basic graph convolution layer for undirected graph without edge labels."""
    def __init__(self, input_dim, output_dim, adj, dropout=0., act=tf.nn.relu, **kwargs):
        super(GraphConvolution, self).__init__(**kwargs)
        with tf.variable_scope(self.name + '_vars'):
            self.vars['weights'] = weight_variable_glorot(input_dim, output_dim, name="weights")
        self.dropout = dropout
        self.adj = adj
        self.act = act

    def _call(self, inputs):
        x = inputs
        x = tf.nn.dropout(x, 1-self.dropout)
        x = tf.matmul(x, self.vars['weights'])
        x = tf.sparse_tensor_dense_matmul(self.adj, x)
        outputs = self.act(x)
        return outputs

class MultiGraphAttention(Layer):
    def __init__(self, input_dim, output_dim, num_head, adj, features_nonzero, sparse=True, dropout=0., concat=True, act=tf.nn.relu, **kwargs):
        super(MultiGraphAttention, self).__init__(**kwargs)
        with tf.variable_scope(self.name + '_vars'):
            weight_l2 = 0.
            for i in range(num_head):
                name = 'l' + str(i)
                self.vars[name] = GraphAttention(input_dim, output_dim, adj, features_nonzero, sparse, dropout, act)
                weight_l2 += tf.nn.l2_loss(self.vars[name].vars['weights'])
                weight_l2 += tf.nn.l2_loss(self.vars[name].vars['a1'])
                weight_l2 += tf.nn.l2_loss(self.vars[name].vars['a2'])
            self.vars['weight_l2'] = weight_l2
        self.concat = concat
        self.num_head = num_head

    def _call(self, inputs):
        output_list = []
        for i in range(self.num_head):
            name = 'l' + str(i)
            output_list.append(self.vars[name](inputs))

        if self.concat:
            return tf.concat(output_list, 1)
        else:
            return tf.add_n(output_list) / len(output_list)

class GraphAttention(Layer):
    def __init__(self, input_dim, output_dim, adj, features_nonzero, sparse=True, dropout=0., act=tf.nn.relu, **kwargs):
        super(GraphAttention, self).__init__(**kwargs)
        with tf.variable_scope(self.name + '_vars'):
            self.vars['weights'] = weight_variable_glorot(input_dim, output_dim, name="weights")
            self.vars['a1'] = weight_variable_glorot(output_dim, 1, name="weights")
            self.vars['a2'] = weight_variable_glorot(output_dim, 1, name="weights")
            self.vars['bias'] = zeros([output_dim], name='bias')
        self.dropout = dropout
        self.adj = adj
        self.act = act
        self.features_nonzero = features_nonzero
        self.sparse = sparse

    def _call(self, inputs):
        x = inputs
        if self.sparse:
            x = dropout_sparse(x, 1-self.dropout, self.features_nonzero)
            x = tf.sparse_tensor_dense_matmul(x, self.vars['weights'])
        else:
            x = tf.nn.dropout(x, 1-self.dropout)
            x = tf.matmul(x, self.vars['weights'])
        a1 = tf.matmul(x, self.vars['a1'])
        a2 = tf.matmul(x, self.vars['a2'])

        ######################
        adj = tf.SparseTensor(self.adj.indices, tf.ceil(self.adj.values), self.adj.dense_shape)
        alpha = tf.sparse_add(adj * a1, adj * tf.transpose(a2), thresh = 0.001)
        
        alpha = tf.SparseTensor(alpha.indices, tf.nn.leaky_relu(alpha.values), alpha.dense_shape)
        alpha = tf.sparse_reorder(alpha)
        alpha = tf.sparse_softmax(alpha)

        alpha = tf.SparseTensor(alpha.indices, tf.nn.dropout(alpha.values, 1-self.dropout), alpha.dense_shape)
        x = tf.nn.dropout(x, 1-self.dropout)
        x = tf.sparse_tensor_dense_matmul(alpha, x)
        ########################

        # alpha = tf.nn.leaky_relu(a1 + tf.transpose(a2))
        # alpha = tf.sparse_tensor_to_dense(alpha, validate_indices = False)
        # adj = tf.ceil(tf.sparse_tensor_to_dense(self.adj, validate_indices = False))
        # bias = tf.exp(adj * -10e9) * -10e9
        # alpha = tf.nn.softmax(alpha + bias)

        # alpha = tf.nn.dropout(alpha, 1 - self.dropout)
        # x = tf.nn.dropout(x, 1-self.dropout)

        # x = tf.matmul(alpha, x)

        x += self.vars['bias']
        #x = tf.contrib.layers.bias_add(x, scope = reuse=True)
        outputs = self.act(x)
        return outputs

class GraphConvolutionSparse(Layer):
    """Graph convolution layer for sparse inputs."""
    def __init__(self, input_dim, output_dim, adj, features_nonzero, dropout=0., act=tf.nn.relu, **kwargs):
        super(GraphConvolutionSparse, self).__init__(**kwargs)
        with tf.variable_scope(self.name + '_vars'):
            self.vars['weights'] = weight_variable_glorot(input_dim, output_dim, name="weights")
        self.dropout = dropout
        self.adj = adj
        self.act = act
        self.issparse = True
        self.features_nonzero = features_nonzero

    def _call(self, inputs):
        x = inputs
        x = dropout_sparse(x, 1-self.dropout, self.features_nonzero)
        x = tf.sparse_tensor_dense_matmul(x, self.vars['weights'])
        x = tf.sparse_tensor_dense_matmul(self.adj, x)
        outputs = self.act(x)
        return outputs

class ScaledInnerProductDecoder(Layer):
    def __init__(self, input_dim, dropout=0., act=tf.nn.sigmoid, **kwargs):
        super(ScaledInnerProductDecoder, self).__init__(**kwargs)
        with tf.variable_scope(self.name + '_vars'):
            scale = zeros(1, name = 'id') + 1
            self.vars['weights'] = scale * scale * tf.eye(input_dim)
        self.dropout = dropout
        self.act = act

    def _call(self, inputs):
        x = tf.transpose(inputs)
        x = tf.matmul(self.vars['weights'], x)
        x = tf.matmul(inputs, x)
        return x

class InnerProductDecoder(Layer):
    """Decoder model layer for link prediction."""
    def __init__(self, input_dim, dropout=0., act=tf.nn.sigmoid, **kwargs):
        super(InnerProductDecoder, self).__init__(**kwargs)
        self.dropout = dropout
        self.act = act

    def _call(self, inputs):
        x = tf.transpose(inputs)
        x = tf.matmul(inputs, x)
        return x
