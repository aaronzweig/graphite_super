from gae.layers import GraphConvolution, GraphConvolutionSparse, InnerProductDecoder
from layers import InnerProductConfigurer, Dense, GraphConvolution, GraphConvolutionSparse, InnerProductDecoder, AutoregressiveDecoder
from layers import AutoregressiveEdgeDecoder
import tensorflow as tf

flags = tf.app.flags
FLAGS = flags.FLAGS


class Model(object):
    def __init__(self, **kwargs):
        allowed_kwargs = {'name', 'logging'}
        for kwarg in kwargs.keys():
            assert kwarg in allowed_kwargs, 'Invalid keyword argument: ' + kwarg

        for kwarg in kwargs.keys():
            assert kwarg in allowed_kwargs, 'Invalid keyword argument: ' + kwarg
        name = kwargs.get('name')
        if not name:
            name = self.__class__.__name__.lower()
        self.name = name

        logging = kwargs.get('logging', False)
        self.logging = logging

        self.vars = {}

    def _build(self):
        raise NotImplementedError

    def build(self):
        """ Wrapper for _build() """
        with tf.variable_scope(self.name):
            self._build()
        variables = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=self.name)
        self.vars = {var.name: var for var in variables}

    def fit(self):
        pass

    def predict(self):
        pass

class GCNModelVAE(Model):
    def __init__(self, placeholders, num_features, num_nodes, features_nonzero, **kwargs):
        super(GCNModelVAE, self).__init__(**kwargs)

        self.inputs = placeholders['features']
        self.input_dim = num_features
        self.features_nonzero = features_nonzero
        self.n_samples = num_nodes
        self.adj = placeholders['adj']
        self.parallel = placeholders['parallel']
        self.dropout = placeholders['dropout']
        self.adj_label = placeholders['adj_orig']
        self.build()

    def encoder(self, inputs):

        hidden1 = GraphConvolutionSparse(input_dim=self.input_dim,
                                              output_dim=FLAGS.hidden1,
                                              adj=self.adj,
                                              features_nonzero=self.features_nonzero,
                                              act=tf.nn.relu,
                                              dropout=self.dropout,
                                              logging=self.logging)(inputs)

        self.z_mean = GraphConvolution(input_dim=FLAGS.hidden1,
                                       output_dim=FLAGS.hidden2,
                                       adj=self.adj,
                                       act=lambda x: x,
                                       dropout=self.dropout,
                                       logging=self.logging)(hidden1)

        self.z_log_std = GraphConvolution(input_dim=FLAGS.hidden1,
                                          output_dim=FLAGS.hidden2,
                                          adj=self.adj,
                                          act=lambda x: x,
                                          dropout=self.dropout,
                                          logging=self.logging)(hidden1)

        self.z_r = GraphConvolution(input_dim=FLAGS.hidden1,
                                          output_dim=1,
                                          adj=self.adj,
                                          act=lambda x: x,
                                          dropout=self.dropout,
                                          logging=self.logging)(hidden1)

        self.z_r_log_std = GraphConvolution(input_dim=FLAGS.hidden1,
                                          output_dim=1,
                                          adj=self.adj,
                                          act=lambda x: x,
                                          dropout=self.dropout,
                                          logging=self.logging)(hidden1)

    def get_z(self, random):

        z = self.z_mean + tf.random_normal([self.n_samples, FLAGS.hidden2]) * tf.exp(self.z_log_std)
        # r = self.z_r + tf.random_normal([self.n_samples, 1]) * tf.exp(self.z_r_log_std)
        if not random:
          z = self.z_mean
          # r = self.z_r

        if FLAGS.sphere_prior:
          z = tf.nn.l2_normalize(z, dim = 1)
          return z
        else:
          return z

    def decoder(self, z):

        reconstructions = InnerProductDecoder(input_dim=FLAGS.hidden2,
                                      act=lambda x: x,
                                      logging=self.logging)(z)

        reconstructions = tf.reshape(reconstructions, [-1])
        return reconstructions

    def _build(self):
  
        self.encoder(self.inputs)
        z = self.get_z(random = True)
        z_noiseless = self.get_z(random = False)
        if not FLAGS.vae:
          z = z_noiseless

        self.reconstructions = self.decoder(z)
        self.reconstructions_noiseless = self.decoder(z_noiseless)

class GCNModelRelnet(GCNModelVAE):
    def __init__(self, placeholders, num_features, num_nodes, features_nonzero, **kwargs):
        super(GCNModelRelnet, self).__init__(placeholders, num_features, num_nodes, features_nonzero, **kwargs)

    def decoder(self, z):

        hidden1 = Dense(input_dim=FLAGS.hidden2,
                                              output_dim=FLAGS.hidden3,
                                              act=tf.nn.relu,
                                              dropout=self.dropout,
                                              logging=self.logging)(z) 

        hidden2 = Dense(input_dim=FLAGS.hidden3,
                                              output_dim=FLAGS.hidden4,
                                              act=lambda x: x,
                                              dropout=self.dropout,
                                              logging=self.logging)(hidden1) 

        reconstructions = InnerProductDecoder(input_dim=FLAGS.hidden4,
                                      act=lambda x: x,
                                      logging=self.logging)(z)

        reconstructions = tf.reshape(reconstructions, [-1])
        return reconstructions

class GCNModelAuto(GCNModelVAE):
    def __init__(self, placeholders, num_features, num_nodes, features_nonzero, **kwargs):
        super(GCNModelAuto, self).__init__(placeholders, num_features, num_nodes, features_nonzero, **kwargs)

    def decoder(self, z):
        reconstructions = AutoregressiveDecoder(input_dim=FLAGS.hidden2,
                                      hidden_dim=FLAGS.hidden3,
                                      hidden_dim2=FLAGS.hidden4,
                                      act=lambda x: x,
                                      adj = self.adj_label,
                                      num_nodes = self.n_samples,
                                      parallel = self.parallel,
                                      logging=self.logging)(z)

        reconstructions = tf.reshape(reconstructions, [-1])
        return reconstructions
