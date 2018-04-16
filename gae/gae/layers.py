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
        adj = inputs[1]
        if self.sparse_inputs:
            x = dropout_sparse(x, 1-self.dropout, self.features_nonzero)
            x = tf.sparse_tensor_dense_matmul(x, self.vars['weights'])
        else:
            x = tf.nn.dropout(x, 1-self.dropout)
            x = tf.matmul(x, self.vars['weights'])
        x = tf.matmul(adj, x)
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

class FiveGraphAttention(Layer):
    def __init__(self, input_dim, output_dim, adj, features_nonzero, dropout=0., act=tf.nn.relu, **kwargs):
        super(FiveGraphAttention, self).__init__(**kwargs)
        with tf.variable_scope(self.name + '_vars'):
            self.vars['weights'] = weight_variable_glorot(input_dim, output_dim, name="weights")
            self.vars['l1'] = GraphAttention(input_dim, output_dim/5, adj, features_nonzero, dropout, act)
            self.vars['l2'] = GraphAttention(input_dim, output_dim/5, adj, features_nonzero, dropout, act)
            self.vars['l3'] = GraphAttention(input_dim, output_dim/5, adj, features_nonzero, dropout, act)
            self.vars['l4'] = GraphAttention(input_dim, output_dim/5, adj, features_nonzero, dropout, act)
            self.vars['l5'] = GraphAttention(input_dim, output_dim/5, adj, features_nonzero, dropout, act)

    def _call(self, inputs):
        return tf.concat((self.vars['l1'](inputs), self.vars['l2'](inputs), self.vars['l3'](inputs), self.vars['l4'](inputs), self.vars['l5'](inputs)), 1)

class GraphAttention(Layer):
    def __init__(self, input_dim, output_dim, adj, features_nonzero, dropout=0., act=tf.nn.relu, **kwargs):
        super(GraphAttention, self).__init__(**kwargs)
        with tf.variable_scope(self.name + '_vars'):
            self.vars['weights'] = weight_variable_glorot(input_dim, output_dim, name="weights")
            self.vars['a1'] = weight_variable_glorot(output_dim, 1, name="weights")
            self.vars['a2'] = weight_variable_glorot(output_dim, 1, name="weights")
        self.dropout = dropout
        self.adj = adj
        self.act = act
        self.features_nonzero = features_nonzero

    def _call(self, inputs):
        x = inputs
        x = dropout_sparse(x, 1-self.dropout, self.features_nonzero)
        x = tf.sparse_tensor_dense_matmul(x, self.vars['weights'])
        a1 = tf.matmul(x, self.vars['a1'])
        a2 = tf.matmul(x, self.vars['a2'])
        alpha = tf.nn.leaky_relu(a1 + tf.transpose(a2))
        adj = tf.sparse_tensor_to_dense(self.adj, validate_indices = False)
        bias = -1e9 * (1.0 - adj)
        alpha = tf.nn.softmax(alpha + adj)
        alpha = tf.nn.dropout(alpha, 1 - self.dropout)
        

        x = tf.matmul(alpha, x)
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
