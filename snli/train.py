import argparse
import logging
import os
import pickle

import tensorboard
from tensorboard import summary

import torch
from torch import nn, optim
from torch.autograd import Variable
from torch.nn.utils import clip_grad_norm
from torch.utils.data import DataLoader

from snli.model import SNLIModel
from snli.utils.dataset import SNLIDataset


logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)-8s %(message)s')


def train(args):
    with open(args.train_data, 'rb') as f:
        train_dataset: SNLIDataset = pickle.load(f)
    with open(args.valid_data, 'rb') as f:
        valid_dataset: SNLIDataset = pickle.load(f)

    train_loader = DataLoader(dataset=train_dataset, batch_size=args.batch_size,
                              shuffle=True, num_workers=2,
                              collate_fn=train_dataset.collate,
                              pin_memory=True)
    valid_loader = DataLoader(dataset=valid_dataset, batch_size=args.batch_size,
                              shuffle=False, num_workers=2,
                              collate_fn=valid_dataset.collate,
                              pin_memory=True)
    word_vocab = train_dataset.word_vocab
    label_vocab = train_dataset.label_vocab

    model = SNLIModel(num_classes=len(label_vocab), num_words=len(word_vocab),
                      word_dim=args.word_dim, hidden_dim=args.hidden_dim,
                      clf_hidden_dim=args.clf_hidden_dim,
                      clf_num_layers=args.clf_num_layers)
    if args.gpu > -1:
        logging.info(f'Using GPU {args.gpu}')
        model.cuda(args.gpu)
    optimizer = optim.Adam(params=model.parameters())
    criterion = nn.CrossEntropyLoss()

    train_summary_writer = tensorboard.FileWriter(
        logdir=os.path.join(args.save_dir, 'train'))
    valid_summary_writer = tensorboard.FileWriter(
        logdir=os.path.join(args.save_dir, 'valid'))

    def wrap_with_variable(tensor, volatile):
        if args.gpu > -1:
            return Variable(tensor.cuda(args.gpu), volatile=volatile)
        else:
            return Variable(tensor, volatile=volatile)

    def unwrap_scalar_variable(var):
        if isinstance(var, Variable):
            return var.data[0]
        else:
            return var

    def run_iter(batch, is_training):
        model.train(is_training)
        pre = wrap_with_variable(batch['pre'], volatile=not is_training)
        hyp = wrap_with_variable(batch['hyp'], volatile=not is_training)
        pre_length = wrap_with_variable(batch['pre_length'],
                                        volatile=not is_training)
        hyp_length = wrap_with_variable(batch['hyp_length'],
                                        volatile=not is_training)
        label = wrap_with_variable(batch['label'], volatile=not is_training)
        logits = model.forward(pre=pre, pre_length=pre_length,
                               hyp=hyp, hyp_length=hyp_length)
        label_pred = logits.max(1)[1].squeeze(1)
        accuracy = torch.eq(label, label_pred).float().mean()
        loss = criterion(input=logits, target=label)
        if is_training:
            optimizer.zero_grad()
            loss.backward()
            clip_grad_norm(parameters=model.parameters(), max_norm=5)
            optimizer.step()
        return loss, accuracy

    def add_scalar_summary(summary_writer, name, value, step):
        value = unwrap_scalar_variable(value)
        summ = summary.scalar(name=name, scalar=value)
        summary_writer.add_summary(summary=summ, global_step=step)

    num_train_batches = len(train_loader)
    validate_every = num_train_batches // 10
    iter_count = 0
    for epoch_num in range(1, args.max_epoch + 1):
        logging.info(f'Epoch {epoch_num}: start')
        for batch_iter, train_batch in enumerate(train_loader):
            train_loss, train_accuracy = run_iter(
                batch=train_batch, is_training=True)
            iter_count += 1
            add_scalar_summary(
                summary_writer=train_summary_writer,
                name='loss', value=train_loss, step=iter_count)
            add_scalar_summary(
                summary_writer=train_summary_writer,
                name='accuracy', value=train_accuracy, step=iter_count)

            if (batch_iter + 1) % validate_every == 0:
                valid_loss_sum = valid_accuracy_sum = 0
                num_valid_batches = len(valid_loader)
                for valid_batch in valid_loader:
                    valid_loss, valid_accuracy = run_iter(
                        batch=valid_batch, is_training=False)
                    valid_loss_sum += unwrap_scalar_variable(valid_loss)
                    valid_accuracy_sum += unwrap_scalar_variable(valid_accuracy)
                valid_loss = valid_loss_sum / num_valid_batches
                valid_accuracy = valid_accuracy_sum / num_valid_batches
                add_scalar_summary(
                    summary_writer=valid_summary_writer,
                    name='loss', value=valid_loss, step=iter_count)
                add_scalar_summary(
                    summary_writer=valid_summary_writer,
                    name='accuracy', value=valid_accuracy, step=iter_count)
                progress = epoch_num + batch_iter/num_train_batches
                logging.info(f'Epoch {progress:.2f}: '
                             f'valid loss = {valid_loss:.4f}, '
                             f'valid accuracy = {valid_accuracy:.4f}')
                model_filename = (f'model-{progress:.2f}'
                                  f'-{valid_loss:.4f}'
                                  f'-{valid_accuracy:.4f}.pkl')
                model_path = os.path.join(args.save_dir, model_filename)
                torch.save(model.state_dict(), model_path)


def main():
    parser = argparse.ArgumentParser(fromfile_prefix_chars='@')
    parser.add_argument('--train-data', required=True)
    parser.add_argument('--valid-data', required=True)
    parser.add_argument('--word-dim', required=True, type=int)
    parser.add_argument('--hidden-dim', required=True, type=int)
    parser.add_argument('--clf-hidden-dim', required=True, type=int)
    parser.add_argument('--clf-num-layers', required=True, type=int)
    parser.add_argument('--gpu', default=-1, type=int)
    parser.add_argument('--batch-size', required=True, type=int)
    parser.add_argument('--max-epoch', required=True, type=int)
    parser.add_argument('--save-dir', required=True)
    args = parser.parse_args()
    train(args)


if __name__ == '__main__':
    main()
