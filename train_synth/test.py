import train_synth.config as config
from src.model import UNetWithResnet50Encoder, Criterian
from train_synth.dataloader import DataLoaderSYNTH
from torch.utils.data import DataLoader
import torch
from tqdm import tqdm
import os
import numpy as np
import matplotlib.pyplot as plt
import random
from src.utils.parallel import DataParallelModel, DataParallelCriterion
from src.utils.utils import calculate_batch_fscore, generate_word_bbox_batch


os.environ['CUDA_VISIBLE_DEVICES'] = str(config.num_cuda)


def save(data, output, target, target_affinity, no):

	"""
	Saving the synthesised outputs in between the training
	:param data: image as tensor
	:param output: predicted output from the model as tensor
	:param target: character heatmap target as tensor
	:param target_affinity: affinity heatmap target as tensor
	:param no: current iteration number
	:return: None
	"""

	output = output.data.cpu().numpy()
	data = data.data.cpu().numpy()
	target = target.data.cpu().numpy()
	target_affinity = target_affinity.data.cpu().numpy()

	batch_size = output.shape[0]

	base = 'test_synthesis/'+str(no)+'/'

	os.makedirs(base, exist_ok=True)

	for i in range(batch_size):
		
		os.makedirs(base+str(i), exist_ok=True)
		character_bbox = output[i, 0, :, :]
		affinity_bbox = output[i, 1, :, :]

		plt.imsave(base+str(i) + '/image.png', data[i].transpose(1, 2, 0))

		plt.imsave(base+str(i) + '/target_characters.png', target[i, :, :], cmap='gray')
		plt.imsave(base+str(i) + '/target_affinity.png', target_affinity[i, :, :], cmap='gray')

		plt.imsave(base + str(i) + '/pred_characters.png', character_bbox, cmap='gray')
		plt.imsave(base + str(i) + '/pred_affinity.png', affinity_bbox, cmap='gray')

		# Thresholding the character and affinity heatmap

		plt.imsave(
			base + str(i) + '/pred_characters_thresh.png',
			np.float32(character_bbox > config.threshold_character), cmap='gray')
		plt.imsave(
			base + str(i) + '/pred_affinity_thresh.png',
			np.float32(affinity_bbox > config.threshold_affinity), cmap='gray')


def test(dataloader, loss_criterian, model):

	"""
	Function to test
	:param dataloader: Pytorch dataloader
	:param loss_criterian: Loss function with OHNM using MSE Loss
	:param model: Pytorch model of UNet-ResNet
	:return: all iteration loss values
	"""

	with torch.no_grad():  # For no gradient calculation

		model.eval()
		iterator = tqdm(dataloader)
		all_loss = []
		all_accuracy = []

		for no, (image, weight, weight_affinity) in enumerate(iterator):

			if config.use_cuda:
				image, weight, weight_affinity = image.cuda(), weight.cuda(), weight_affinity.cuda()

			output = model(image)
			loss = loss_criterian(output, weight, weight_affinity).mean()

			all_loss.append(loss.item())

			if type(output) == list:
				output = torch.cat(output, dim=0)

			predicted_bbox = generate_word_bbox_batch(
				output[:, 0, :, :].data.cpu().numpy(),
				output[:, 1, :, :].data.cpu().numpy(),
				character_threshold=config.threshold_character,
				affinity_threshold=config.threshold_affinity)

			target_bbox = generate_word_bbox_batch(
				weight.data.cpu().numpy(),
				weight_affinity.data.cpu().numpy(),
				character_threshold=config.threshold_character,
				affinity_threshold=config.threshold_affinity)

			all_accuracy.append(calculate_batch_fscore(predicted_bbox, target_bbox, threshold=config.threshold_fscore))

			iterator.set_description(
				'Loss:' + str(int(loss.item() * 100000000) / 100000000) + ' Iterations:[' + str(no) + '/' + str(
					len(iterator)) +
				'] Average Loss:' + str(int(np.array(all_loss)[-min(1000, len(all_loss)):].mean()*100000000)/100000000) +
				'| Average F-Score: ' + str(int(np.array(all_accuracy)[-min(1000, len(all_accuracy)):].mean()*100000000)/100000000)
			)

			if no % config.periodic_output == 0 and no != 0:
				if type(output) == list:
					output = torch.cat(output, dim=0)
				save(image, output, weight, weight_affinity, no)

		return all_loss


def seed():

	# This removes randomness, makes everything deterministic

	np.random.seed(config.seed)
	random.seed(config.seed)
	torch.manual_seed(config.seed)
	torch.cuda.manual_seed(config.seed)
	torch.backends.cudnn.deterministic = True


def main(model_path):

	seed()

	model = UNetWithResnet50Encoder()

	model_parameters = filter(lambda p: p.requires_grad, model.parameters())
	params = sum([np.prod(p.size()) for p in model_parameters])

	print('Total number of trainable parameters: ', params)

	model = DataParallelModel(model)
	loss_criterian = DataParallelCriterion(Criterian())

	test_dataloader = DataLoaderSYNTH('test')

	if config.use_cuda:
		model = model.cuda()

	test_dataloader = DataLoader(
		test_dataloader, batch_size=config.batch_size['test'],
		shuffle=True, num_workers=16)

	saved_model = torch.load(model_path)
	model.load_state_dict(saved_model['state_dict'])

	all_loss = test(test_dataloader, loss_criterian, model)

	print('Average Loss on the testing set is:', all_loss)