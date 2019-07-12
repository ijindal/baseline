import torch
import torch.nn as nn
import logging
import numpy as np
from baseline.train import create_trainer, register_trainer, register_training_func, Trainer
from baseline.pytorch.classify import ClassifierModelBase
from baseline.confusion import ConfusionMatrix
from baseline.utils import listify, get_model_file, write_json, color, Colors, get_metric_cmp
from baseline.reporting import register_reporting, ReportingHook
from baseline.model import register_model
from baseline.pytorch.torchy import *
from collections import OrderedDict
logger = logging.getLogger('baseline')

def minDiagonalSwap(nums, index):
    """ Return inplace swaped.

    Swap the diagonal element with the minimum element in the array
    return inplace altered list.
    """
    ind = np.argmin(nums)
    temp = nums[index]
    nums[index] = nums[ind]
    nums[ind] = temp
    return


class InjectNoise(object):
    # class for injecting artificial noise to the clean labels.

    def __init__(self, y_train, n_type, noiselvl, noise_mat=np.zeros(0)):
        """
        type(n_type): str: noise type ['uni','rand','CC']
        type(y_train): Numpy array: clean training labels
        type(noiselvl): float: input levelof noise
        """
        self.n_cls = len(list(set(y_train)))
        self.type = n_type
        self.noiselvl = noiselvl
        self.true_label = y_train
        if not noise_mat.any():
            self.noise_matrix = self.gen_noise_matrix()
        else:
            self.noise_matrix = noise_mat
        # print(self.noise_matrix)
        self.noisy_label = self.gen_labels()
        print('object created')

    def gen_noise_matrix(self):
        """Generate the random/uniform noise matrix
        return: a stochastic matrix based on the type of noise.
        """
        n_class = self.n_cls
        noise_matrix = np.zeros([n_class, n_class], dtype='float')

        if self.n_cls <= 2:
            noise_matrix = (1-self.noiselvl)*np.eye(n_class, dtype='float') + self.noiselvl*(np.ones(n_class, dtype='float') - np.eye(n_class, dtype='float'))
        else:
            # Defines random noise over unit simplex
            if self.type == 'rand':
                for a in range(n_class):
                    nums = [np.random.randint(0, 10)*1.0 for x in  range(n_class)]
                    # print(nums[1]/sum(np.array(nums)))
                    nums = self.noiselvl*(nums/sum(np.array(nums)))
                    # print(nums)
                    minDiagonalSwap(nums, a)
                    noise_matrix[a, :] = nums
                    # print(noise_matrix)
                noise_matrix = (1-self.noiselvl)*np.eye(n_class, dtype='float') + noise_matrix

            # defines uniform noise
            elif self.type == 'uni':
                noise_matrix = ((1-self.noiselvl)*np.eye(n_class, dtype='float') + (self.noiselvl/n_class)*(np.ones(n_class, dtype='float')))
            # TO-DO
            # define other type of noise
            elif self.type == 'cc':
                levels = [np.random.randint(0,n_class)*1.0 for i in range(n_class)]
                levels = n_class*self.noiselvl*(levels/sum(np.array(levels)))
                ind = 0
                for n in levels:
                    nums = [np.random.randint(0, 10)*1.0 for x in  range(n_class)]
                    # print(nums[1]/sum(np.array(nums)))
                    nums = n*(nums/sum(np.array(nums)))
                    # print(nums)
                    minDiagonalSwap(nums, ind)
                    noise_matrix[ind, :] =(1-n)*np.eye(n_class, dtype='float')[ind] + nums
                    ind = ind + 1
                print(noise_matrix)
            else:
                print("Please define correct form of noise ['uni','rand','cc']")
                return None
        return noise_matrix

    def gen_labels(self):
        """Generate noisy labels for the training and valid set
        return: Array of noisy labels.
        """

        noisy_label = np.zeros_like(self.true_label)
        classes = list(set(self.true_label))

        for cls in range(self.n_cls):
            flip_label = []
            n_cls_labels = sum(self.true_label == classes[cls])

            for _ in range(n_cls_labels):
                flip_label.append(np.random.choice(np.arange(0, self.n_cls), p=self.noise_matrix[cls,:]))

            flip_label = np.array(flip_label)

            noisy_label[self.true_label == cls] = flip_label.astype(int)
            print('Number of labels for class ', cls, ' flipped: ', n_cls_labels - sum(flip_label == cls), 'out of ', n_cls_labels)

        return noisy_label


def injectlabelnoise(ts, vs, noise_level, noise_type):
    """
    :param ts: Input pyt training data
    :param vs: Input Valid data
    :param noise_level: <int> Noise level
    :param noise_type: <str> Noise type ('uni','rand', 'cc')
    :return: train and valid dataset with label noise injected
    """
    # np.random.seed(7)
    train_y = []
    valid_y = []
    for i in range(len(ts.examples)):
        train_y.append(ts.examples[i]['y'])
    for i in range(len(vs.examples)):
        valid_y.append(vs.examples[i]['y'])

    train_noise = InjectNoise(np.array(train_y), n_type=noise_type, noiselvl=noise_level)
    valid_noise = InjectNoise(np.array(valid_y), n_type=noise_type, noiselvl=noise_level,
                               noise_mat=train_noise.noise_matrix)
    print("Injected label transition matrix: ", train_noise.noise_matrix)

    for i in range(len(ts.examples)):
        ts.examples[i]['y'] = int(train_noise.noisy_label[i])
    for i in range(len(vs.examples)):
        vs.examples[i]['y'] = int(valid_noise.noisy_label[i])

    return ts, vs

@register_model(task='classify', name='noisyconv')
class NoisyConvClassifier(ClassifierModelBase):

    def __init__(self):
        super(NoisyConvClassifier, self).__init__()


    def init_output(self, input_dim, nc):
        self.output = nn.Sequential(OrderedDict([
            ('linear1', nn.Linear(input_dim, nc)),
            ('softmax', nn.Softmax(dim=1)),
            ('linear2', nn.Linear(nc, nc, bias=False)),
            ('logSoftmax', nn.LogSoftmax(dim=1))]))


    def init_pool(self, dsz, **kwargs):
        filtsz = kwargs['filtsz']
        cmotsz = kwargs['cmotsz']
        self.parallel_conv = ParallelConv(dsz, cmotsz, filtsz, "relu", self.pdrop)
        return self.parallel_conv.outsz

    def pool(self, btc, lengths):
        embeddings = btc.transpose(1, 2).contiguous()
        return self.parallel_conv(embeddings)

@register_training_func(task='classify', name='train-noisy-model')
def fit(model, ts, vs, es=None, **kwargs):
    """
    Train a classifier using PyTorch
    :param model: The model to train
    :param ts: A training data set
    :param vs: A validation data set
    :param es: A test data set, can be None
    :param kwargs: See below

    :Keyword Arguments:
        * *do_early_stopping* (``bool``) -- Stop after eval data is not improving. Default to True
        * *epochs* (``int``) -- how many epochs.  Default to 20
        * *outfile* -- Model output file, defaults to classifier-model.pyth
        * *patience* --
           How many epochs where evaluation is no longer improving before we give up
        * *reporting* --
           Callbacks which may be used on reporting updates
        * *optim* --
           Optimizer to use, defaults to `sgd`
        * *eta, lr* (``float``) --
           Learning rate, defaults to 0.01
        * *mom* (``float``) --
           Momentum (SGD only), defaults to 0.9 if optim is `sgd`
    :return:
    """
    noise_level = kwargs.get('noiselvl', 0.0)
    weight_decay = kwargs.get('nmpenality', 0.0)
    noise_type = kwargs.get('noisetyp', 'uni')

    do_early_stopping = bool(kwargs.get('do_early_stopping', True))
    verbose = kwargs.get('verbose',
                         {'console': kwargs.get('verbose_console', False), 'file': kwargs.get('verbose_file', None)})
    epochs = int(kwargs.get('epochs', 20))
    model_file = get_model_file('classify', 'pytorch', kwargs.get('basedir'))
    output = kwargs.get('output')
    txts = kwargs.get('txts')

    best_metric = 0
    if do_early_stopping:
        early_stopping_metric = kwargs.get('early_stopping_metric', 'acc')
        early_stopping_cmp, best_metric = get_metric_cmp(early_stopping_metric, kwargs.get('eatly_stopping_cmp'))
        patience = kwargs.get('patience', epochs)
        logger.info('Doing early stopping on [%s] with patience [%d]', early_stopping_metric, patience)

    reporting_fns = listify(kwargs.get('reporting', []))
    logger.info('reporting %s', reporting_fns)

    trainer = create_trainer(model, **kwargs)

    last_improved = 0

    if noise_level > 0:
        ts, vs = injectlabelnoise(ts, vs, noise_level, noise_type)

    for epoch in range(epochs):
        trainer.train(ts, reporting_fns)
        test_metrics = trainer.test(vs, reporting_fns)

        if do_early_stopping is False:
            model.save(model_file)

        elif early_stopping_cmp(test_metrics[early_stopping_metric], best_metric):
            last_improved = epoch
            best_metric = test_metrics[early_stopping_metric]
            logger.info('New best %.3f', best_metric)
            model.save(model_file)

        elif (epoch - last_improved) > patience:
            logger.info('Stopping due to persistent failures to improve')
            break

    if do_early_stopping is True:
        logger.info('Best performance on %s: %.3f at epoch %d', early_stopping_metric, best_metric, last_improved)

    if es is not None:
        logger.info('Reloading best checkpoint')
        model = torch.load(model_file)
        trainer = create_trainer(model, **kwargs)
        test_metrics = trainer.test(es, reporting_fns, phase='Test', verbose=verbose, output=output, txts=txts)
    return test_metrics

