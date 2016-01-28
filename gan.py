import theano
theano.config.on_unused_input = 'ignore'
import numpy as np
import cPickle as pickle
import theano
import sys
import csv
import logging
import random
import Tkinter
from dataset import *
from batcher import *
from deepx.nn import *
from deepx.rnn import *
from deepx.loss import *
from deepx.optimize import *
from argparse import ArgumentParser

logging.basicConfig(level=logging.DEBUG)


def parse_args():
    argparser = ArgumentParser()
    argparser.add_argument('--sequence_length', default=100)
    argparser.add_argument('--batch_size', default=100)
    argparser.add_argument('--log', default='loss/gan_log_current.txt')
    return argparser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    logging.debug('Retrieving text encoding...')
    with open('data/charnet-encoding.pkl', 'rb') as fp:
        text_encoding = pickle.load(fp)

    logging.debug('Compiling discriminator...')
    discriminator = Sequence(Vector(len(text_encoding))) >> (Repeat(LSTM(1024), 2) >> Softmax(2))
    
    logging.debug('Compiling generator...')
    generator = Generate(Vector(len(text_encoding)) >> Repeat(LSTM(1024), 2) >> Softmax(len(text_encoding)), args.sequence_length)
    
    logging.debug('Compiling GAN...')
    gan = generator >> discriminator.right

    # Fix discriminator weights while training generator
    gan.right.frozen = True
    print gan.get_parameters()
    # Optimization procedure
    rmsprop_G = RMSProp(gan, ConvexSequentialLoss(CrossEntropy(), 0.5))

    # Unfreeze the discrimator
    gan.right.frozen = False
    print discriminator.get_parameters()
    # Optimization procedure
    rmsprop_D = RMSProp(discriminator, ConvexSequentialLoss(CrossEntropy(), 0.5), clip_gradients=5)


    ##########
    # Stage I 
    ##########
    # Load parameters after chaining operations due to known issue in DeepX
    with open('models/generative-model-0.0.pkl', 'rb') as fp:
        generator.set_state(pickle.load(fp))

    with open('models/discriminative-model-0.0.pkl', 'rb') as fp:
        state = pickle.load(fp)
        state = (state[0][0], (state[0][1], state[1]))
        discriminator.set_state(state)
    

    def generate_sample():
        '''Generate a sample from the current version of the generator'''
        pred_seq = generator.predict(np.eye(100)[None,0])
        num_seq  = NumberSequence(pred_seq.argmax(axis=2).ravel()).decode(text_encoding)
        return ''.join(num_seq.seq)


    def generate_fake_reviews(num_reviews):
        '''Generate fake reviews using the current generator'''
        fake_reviews = []
        
        for _ in xrange(num_reviews):
            review = generate_sample()
            fake_reviews.append(review)
        
        fake_reviews = [r.replace('\x05',  '') for r in fake_reviews]
        fake_reviews = [r.replace('<STR>', '') for r in fake_reviews]
        return fake_reviews


    def predict(text):
        '''Return prediction array at each time-step of input text'''
        char_seq   = CharacterSequence.from_string(text)
        num_seq    = char_seq.encode(text_encoding)
        num_seq_np = num_seq.seq.astype(np.int32)
        X          = np.eye(len(text_encoding))[num_seq_np]
        return discriminator.predict(X)


    ###########
    # Stage II 
    ###########

    def train_generator(iterations, step_size):
        '''Train the generative model via a GAN framework'''  
        with open(args.log, 'a+') as fp:
            for _ in xrange(iterations):
                index = text_encoding.encode('<STR>')
                batch = np.tile(text_encoding.convert_representation([index]), (args.batch_size, 1))
                y = np.tile([0, 1], (args.sequence_length, args.batch_size, 1))
                loss = rmsprop_G.train(batch, y, step_size)
                print >> fp,  "Generator Loss[%u]: %f" % (_, loss)
                print "Generator Loss[%u]: %f" % (_, loss)
                fp.flush()
        
        
    def train_discriminator(iterations, step_size, real_reviews, fake_reviews):
        '''Train the discriminator on real and fake reviews'''
        random.seed(1)
        
        # Load and shuffle reviews
        real_targets, fake_targets = [],  []
        for _ in xrange(len(real_reviews)):
            real_targets.append([0, 1])
        for _ in xrange(len(fake_reviews)):
            fake_targets.append([1, 0])

        all_reviews = zip(real_reviews, real_targets) + zip(fake_reviews, fake_targets)
        random.shuffle(all_reviews)
        
        reviews, targets = zip(*all_reviews[:100]) #TEMP:  Just testing

        logging.debug("Converting to one-hot...")
        review_sequences = [CharacterSequence.from_string(review) for review in reviews]
        num_sequences = [c.encode(text_encoding) for c in review_sequences]
        target_sequences = [NumberSequence([target]).replicate(len(r)) for target, r in zip(targets, num_sequences)]
        final_seq = NumberSequence(np.concatenate([c.seq.astype(np.int32) for c in num_sequences]))
        final_target = NumberSequence(np.concatenate([c.seq.astype(np.int32) for c in target_sequences]))

        # Construct the batcher
        batcher = WindowedBatcher([final_seq], [text_encoding], final_target, sequence_length=200, batch_size=100)
        
        with open(args.log, 'a+') as fp:
            for _ in xrange(iterations):
                X, y = batcher.next_batch()
                loss = rmsprop_D.train(X, y, step_size)
                print >> fp,  "Discriminator Loss[%u]: %f" % (_, loss)
                print "Discriminator Loss[%u]: %f" % (_, loss)
                fp.flush()


    def alternating_gan(num_iter):
        '''Alternating GAN procedure for jointly training the generator (G) 
        and the discriminator (D)'''

        logging.debug('Loading real reviews...')
        with open('data/real_beer_reviews.txt', 'r') as f:
            real_reviews = [r[3:] for r in f.read().strip().split('\n')]
            real_reviews = [r.replace('\x05',  '') for r in real_reviews] 
            real_reviews = [r.replace('<STR>', '') for r in real_reviews]

        real_reviews = real_reviews[0:100] #TEMP:  Just testing

        with open(args.log, 'w') as fp:
            print >> fp, 'Alternating GAN for ',num_iter,' iterations.'

        for i in xrange(num_iter):
            logging.debug('Training generator...')
            #TEMP:  Eventually have stopping criterion
            train_generator(25, 1) 
            
            logging.debug('Generating new fake reviews...')
            fake_reviews = generate_fake_reviews(100)            

            logging.debug('Training discriminator...')
            #TEMP:  Eventually have stopping criterion
            train_discriminator(25, 100, real_reviews, fake_reviews)


            with open('models/gan-model-current.pkl', 'wb') as f:
                pickle.dump(gan.get_state(), f)



