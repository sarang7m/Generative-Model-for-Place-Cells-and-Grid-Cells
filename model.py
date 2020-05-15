import torch
import torch.nn as nn
from torch.nn import init
import torchvision
import torch.nn.functional as F
import torchvision.transforms as T
import torch.optim as optim
import time
import numpy as np
import pyflann
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from config import *
import roam


########################################################################################

# processing the image
class preprocessImage(nn.Module):
	
	def forward(self,x):
		return x * 2 - 1
		
# deprocessing the image		
class deprocessImage(nn.Module):

	def forward(self,x):
		return (x+1) / 2
		
# exponent
class exponent(nn.Module):
	
	def forward(self,x):
		return torch.exp(x)
		

# flatten 
class flatten(nn.Module):
	
	def forward(self,x):
	
		N,C,H,W = x.size()
		return x.contiguous().view(N,-1)
		# contiguous() does not change the target tensor but makes sure that data in tensor
		# is stored in contiguous memory.
		
# unflatten
class unflatten(nn.Module):
	def __init__(self, N=-1, C=3, H=8, W=8):
	
		# converts size from N,C*H*W to N*C*H*W
		
		super(Unflatten, self).__init__()
		
		self.N = N
		self.C = C
		self.H = H
		self.W = W
		
	def forward(self,x):
		return x.view(self.N,self.C,self.H,self.W)
			

##################################################################################

class GTMSM(nn.Module):

	# x_dim is used nowhere.
	def __init__(self, x_dim = 8, s_dim = 2, z_dim = 8, observe_dim = 100, total_dim = 150, r_std = 0.001, k_nearesr_neighbor = 5, delta = 0.0001, kl_samples = 100, batch_size = 1,lambda_for_mat_orth = 1000,lambda_for_mat_mag = 1000, lambda_for_sigmoid = 10000):
	
	
		super(GTMSM, self).__init__()
	
		self.x_dim = x_dim # not used
		self.a_dim = a_dim
		self.s_dim = s_dim
		self.z_dim = z_dim
		self.observe_dim = observe_dim
		self.z_dim = z_dim
		self.total_dim = total_dim
		self.r_std = r_std
		self.k_nearest_neighbor = 5
		self.delta = delta
		self.kl_samples = kl_samples
		self.batch_size = batch_size
		self.lambda_for_math_orth = lambda_for_mat_orth
		self.lambda_for_sigmoid = lambda_for_sigmoid
		
		self.flanns = pyflann.FLANN()
		
		
		
		#encoder
		
		# getting zt from xt
		self.enc_zt = nn.Sequential(
			preprocessImage(),
			nn.Conv2d(3,16,kernel_size = 1,stride=1),
			nn.LeakyReLU(0.01),
			nn.Conv2d(16,8,kernel_size=1,stride=1),
			nn.LeakyReLU(0.01),
			Flatten())
		
		self.enc_zt_mean = nn.Sequential(
			nn.linear(16,z_dim)) # in features, out features
			
		self.enc_zt_std = nn.Sequential(nn.linear(16,z_dim), Exponent())
		
		
		# getting st from a
		self.enc_st_matrix = nn.Sequential(nn.Linear(a_dim,s_dim, bias = False))
		
		self.enc_st_sigmoid = nn.Sequential(
			nn.Linear(s_dim,10),
			nn.ReLU(),
			nn.Linear(10,5),
			nn.Relu(),
			nn.Linear(5,1),
			nn.Sigmoid())
			
		# decoder
		
		self.dec = nn.sequential(
			nn.Linear(z_dim,16),
			nn.Relu(),
			Unflatten(-1,8,2,2),
			nn.ConvTranspose2d(in_channels = 8, out_channels = 16, kernel_size = 1, stride=1),
			nn.ReLU(),
			nn.ConvTranpose2d(in_channels = 16, out_channels = 3,kernel_size = 1, stride=1),
			nn.Tanh(),
			deprocessImage())
			
			
	def forward(self, x):
		
		if not self.training :
			original_total_dim = self.total_dim
		if len(x.shape) == 3:
			x = x.unsqueeze(0)
			
		action_one_hot_value, position, action_selection = roam.move(batch_size, total_dim)
		
			
			
		st_observation_list = []
		st_prediction_list = []
		zt_mean_observation_list = []
		zt_std_observation_list = []
		zt_mean_prediction_list = []
		zt_std_prediction_list = []
		xt_prediction_list = []
		
		kld_loss = []
		nll_loss = []
		
		
		# observation phase : constructing st
		
		
		for t in range(self.observe_dim):
			
			if t == 0:
			
				st_observation_t = torch.zeros(self.batch_size, self.s_dim, device = device)
				
			else :
			
				replacement = self.enc_st_matrix(action_one_hot_value[:,:,t-1])
				
				st_observation_t = st_observation_list[t-1] + replacement + torch.randn((self.batch_size, self.s_dim), device = device) * self.r_std
				
			st_observation_list.append(st_observation_t)
		st_observation_error_tensor = torch.cat(st_observation_list, 0).view(self.obersve_dim, self.batch_size, self.s_dim)
		
		
		
		# prediction phase : construct st
		
		for t in range(self.total_dim - self.observe_dim):
		
			if t == 0:
			
				replacement = self.enc_st_matrix(action_one_hot_value[:,:,t + self.observe_dim -1])
				
				st_prediction_t = st_observation_list[-1] + replacement + \
				torch.randn((self.batch_size, self.s_dim), deice = device) * self.r_std
				
			else :
			
				st_prediction_t = st_prediction_list[t - 1] + replacement + torch.randn((self.batch_size, self.s_dim), device=device) * self.r_std
				
			st_prediction_list.append(st_prediction_t)
		st_prediction_tensor = torch.cats(st_prediction_list, 0).view(self.total_dim - self.observe_dim, self.batch_size, self.s_dim)
		
		
		
		# observation phase : constructing zt from xt
		
		for t in range(self.observe_dim):
		
			index_mask = torch.zeros((self.batch_size, 3, 8, 8), device=device)
        	
			for index_sample in range(self.batch_size):
				position_h_t = position[index_sample, 0, t]
				position_w_t = position[index_sample, 1, t]
				index_mask[index_sample, :, 3 * position_h_t:3 * position_h_t + 2,
                3 * position_w_t:3 * position_w_t + 2] = 1
						
			index_mask_bool = index_mask.ge(0.5)
			x_feed = torch.masked_select(x, index_mask_bool).view(-1, 3, 2, 2)
			zt_observation_t = self.enc_zt(x_feed)
			zt_mean_observation_t = self.enc_zt_mean(zt_observation_t)
			zt_std_observation_t = self.enc_zt_std(zt_observation_t)
			zt_mean_observation_list.append(zt_mean_observation_t)
			zt_std_observation_list.append(zt_std_observation_t)				
						
						
						
		zt_mean_observation_tensor = torch.cat(zt_mean_observation_list, 0).view(self.observe_dim, self.batch_size,
                                                                                 self.z_dim)
		zt_std_observation_tensor = torch.cat(zt_std_observation_list, 0).view(self.observe_dim, self.batch_size,
                                                                               self.z_dim)
		
		
								   
				
			
		if self.training :
			# constructing zt from xt
			
			for t in range(self.total_dim - self.observe_dim):
				index_mask = torch.zeros((self.batch_size, 3, 8, 8), device=device)
				for index_sample in range(self.batch_size):
					position_h_t = position[index_sample, 0, t + self.observe_dim]
					position_w_t = position[index_sample, 1, t + self.observe_dim]
					index_mask[index_sample, :, 3 * position_h_t:3 * position_h_t + 2,
					3 * position_w_t:3 * position_w_t + 2] = 1
					
					
					
				index_mask_bool = index_mask.ge(0.5)
				x_feed = torch.masked_select(x, index_mask_bool).view(-1, 3, 2, 2)
				zt_prediction_t = self.enc_zt(x_feed)
				zt_mean_prediction_t = self.enc_zt_mean(zt_prediction_t)
				zt_std_prediction_t = self.enc_zt_std(zt_prediction_t)
				zt_mean_prediction_list.append(zt_mean_prediction_t)
				zt_std_prediction_list.append(zt_std_prediction_t)
					
	
					
			zt_mean_prediction_tensor = torch.cat(zt_mean_prediction_list, 0).view(self.total_dim - self.observe_dim,self.batch_size, self.z_dim)

			zt_std_prediction_tensor = torch.cat(zt_std_prediction_list, 0).view(self.total_dim - self.observe_dim,self.batch_size, self.z_dim)
			
			
			
			# reparameterize to calculate sample
			
			
			for t in range(self.total_dim - self.observe_dim):
				zt_prediction_sample = self._reparameterized_sample(zt_mean_prediction_list[t],zt_std_prediction_list[t])
				index_mask = torch.zeros((self.batch_size, 3, 8, 8), device=device)
				for index_sample in range(self.batch_size):
					position_h_t = position[index_sample, 0, t + self.observe_dim]
					position_w_t = position[index_sample, 1, t + self.observe_dim]
					index_mask[index_sample, :, 3 * position_h_t:3 * position_h_t + 2,
					3 * position_w_t:3 * position_w_t + 2] = 1
				index_mask_bool = index_mask.ge(0.5)
				x_ground_true_t = torch.masked_select(x, index_mask_bool).view(-1, 3, 2, 2)
				x_resconstruct_t = self.dec(zt_prediction_sample)
				nll_loss += self._nll_gauss(x_resconstruct_t, x_ground_true_t)
				xt_prediction_list.append(x_resconstruct_t)
				
				
			# connstructing the kd tree
			
			st_observation_memory = st_obervation_tensor.cpu().detach().numpy()
			st_prediction_memory = st_prediction_tensor.cpu().detach().numpy()
			
			
			results = []
			for index_sample in range(self.batch_size):
			
				param = self.flanns.build_index(st_observation_memory[:, index_sample, :], algorithm='kdtree',trees=4)
				result, _ = self.flanns.nn_index(st_prediction_memory[:, index_sample, :],
                                             self.k_nearest_neighbour, checks=param["checks"])
                                             
				results.append(result)
				
				
				
			if self.training :
			
				# calculate kld
				for index_sample in range(sself_batch_size):
				
					knn_index = results[index_sample]
					knn_index_vec = np.reshape(knn_index, (self.k_nearest_neighbour * (self.total_dim - self.observe_dim)))
					knn_st_memory = (st_observation_tensor[knn_index_vec, index_sample]).reshape((self.total_dim - self.observe_dim),self.k_nearest_neighbour, -1)
					dk2 = ((knn_st_memory.transpose(0, 1) - st_prediction_tensor[:, index_sample, :]) ** 2).sum(2).transpose(0, 1)
					wk = 1 / (dk2 + self.delta)
					normalized_wk = (wk.t() / torch.sum(wk, 1)).t()
					log_normalized_wk = torch.log(normalized_wk)
					zt_sampling = self._reparameterized_sample_cluster(zt_mean_prediction_tensor[:, index_sample],zt_std_prediction_tensor[:, index_sample])
					log_q_phi = - 0.5 * self.z_dim * torch.log(torch.tensor(2 * 3.1415926535, device = device)) - 0.5 * self.z_dim - torch.log(zt_std_prediction_tensor[:, index_sample]).sum(1)
				
					zt_mean_knn_tensor = zt_mean_observation_tensor[knn_index_vec, index_sample].reshape((self.total_dim - self.observe_dim), self.k_nearest_neighbour, -1)
					zt_std_knn_tensor = zt_std_observation_tensor[knn_index_vec, index_sample].reshape((self.total_dim - self.observe_dim), self.k_nearest_neighbour, -1)
					log_p_theta_element = self._log_gaussian_element_pdf(zt_sampling, zt_mean_knn_tensor, zt_std_knn_tensor) + log_normalized_wk
					(log_p_theta_element_max, _) = torch.max(log_p_theta_element, 2)
					log_p_theta_element_nimus_max = (log_p_theta_element.transpose(1, 2).transpose(0, 1) - log_p_theta_element_max)
					p_theta_nimus_max = torch.exp(log_p_theta_element_nimus_max).sum(0)
					kld_loss += torch.mean(log_q_phi - torch.mean(log_p_theta_element_max + torch.log(p_theta_nimus_max), 0))
				
				
			else :
			
				xt_prediction_tensor = torch.zeros(self.total_dim - self.observe_dim, self.batch_size, 3, 8, 8,device=device) 
				
				for index_sample in range(self.batch_size) :
				
					knn_index = results[index_sample]
					knn_index_vec = np.reshape(knn_index, (self.k_nearest_neighbour * (self.total_dim - self.observe_dim)))
					knn_st_memory = (st_observation_tensor[knn_index_vec, index_sample]).reshape((self.total_dim - self.observe_dim),self.k_nearest_neighbour, -1)
					dk2 = ((knn_st_memory.transpose(0, 1) - st_prediction_tensor[:, index_sample, :]) ** 2).sum( 2).transpose(0, 1)  
					wk = 1 / (dk2 + self.delta)
					normalized_wk = (wk.t() / torch.sum(wk, 1)).t()
					cumsum_normalized_wk = torch.cumsum(normalized_wk, dim=1)
					rand_sample_value = torch.rand((self.total_dim - self.observe_dim, 1), device=device)
					
					
					bool_index_list = cumsum_normalized_wk + torch.tensor(1e-7).to(device=device) <= rand_sample_value
				 
					knn_sample_index = bool_index_list.sum(1)
					zt_sampling = self._reparameterized_sample(
						zt_mean_observation_tensor[knn_index[range(self.total_dim - self.observe_dim), knn_sample_index], index_sample],
						zt_std_observation_tensor[knn_index[range(self.total_dim - self.observe_dim), knn_sample_index], index_sample])
			
					xt_prediction_tensor[:, index_sample] = self.dec(zt_sampling)
					
				
				# calculate reconstructing error
				
				for t in range(self.total_dim - self.observe_dim):
					
					index_mask = torch.zeros((self.batch_size, 3, 32, 32), device=device)
					for index_sample in range(self.batch_size):
						 position_h_t = position[index_sample, 0, t + self.observe_dim]
						 position_w_t = position[index_sample, 1, t + self.observe_dim]
						 index_mask[index_sample, :, 3 * position_h_t:3 * position_h_t + 8,3 * position_w_t:3 * position_w_t + 2] = 1
					index_mask_bool = index_mask.ge(0.5)
					x_ground_true_t = torch.masked_select(x, index_mask_bool).view(-1, 3, 2, 2)	
					nll_loss += self._nll_gauss(xt_prediction_tensor[t], x_ground_true_t)
					
					
				# reparmeterizing to calculate the error
				
				for t in range(self.total_dim - self.observe_dim):
					 xt_prediction_list.append(xt_prediction_tensor[t])
					 
			if not self.training:
				self.total_dim = origin_total_dim
				
			matrix_loss = self._matrix_loss()
			return kld_loss, nll_loss, matrix_loss, st_observation_list, st_prediction_list, xt_prediction_list, position
			
			
			
	def _log_gaussian_pdf(self, zt, zt_mean, zt_std):
		constant_value = torch.tensor(2 * 3.1415926535, device = device)
		log_exp_term = - torch.sum((((zt - zt_mean) ** 2) / (zt_std ** 2) / 2.0), 2)
		log_other_term = - (self.z_dim / 2.0) * torch.log(constant_value) - torch.sum(torch.log(zt_std), 1)
		return log_exp_term + log_other_term

	def _log_gaussian_element_pdf(self, zt, zt_mean, zt_std):
		constant_value = torch.tensor(2 * 3.1415926535, device = device)
		zt_repeat = zt.unsqueeze(2).repeat(1, 1, self.k_nearest_neighbour, 1)
		log_exp_term = - torch.sum((((zt_repeat - zt_mean) ** 2) / (zt_std ** 2) / 2.0), 3)
		log_other_term = - (self.z_dim / 2.0) * torch.log(constant_value) - torch.sum(torch.log(zt_std), 2)
		return log_exp_term + log_other_term

	def reset_parameters(self, stdv=1e-1):
		for weight in self.parameters():
			weight.data.normal_(0, stdv)

	def _init_weights(self, stdv):
		for weight in self.parameters():
			weight.data.normal_(0, stdv)

	def _reparameterized_sample(self, mean, std):

		eps = torch.randn_like(std, device = device)
		return eps.mul(std).add(mean)


	def _reparameterized_sample_cluster(self, mean, std):

		eps = torch.randn((self.kl_samples, self.total_dim - self.observe_dim, self.z_dim), device=device)
		return eps.mul(std).add(mean)


	def _kld_gauss(self, mean_1, std_1, mean_2, std_2):

		kld_element = (2 * torch.log(std_2) - 2 * torch.log(std_1) +
                       (std_1.pow(2) + (mean_1 - mean_2).pow(2)) /
                       std_2.pow(2) - 1)
		return 0.5 * torch.sum(kld_element)

	def _nll_bernoulli(self, theta, x):
		return - torch.sum(x * torch.log(theta) + (1 - x) * torch.log(1 - theta))

	def _nll_gauss(self, x, mean):

		return torch.sum((x - mean) ** 2)

	def _matrix_loss(self):

		for param in self.enc_st_matrix.parameters():
			matrix_loss = self.lambda_for_mat_orth * torch.sum(torch.sum(param[:, 0:1] * param[:, 2:3], 0) ** 2)
		for index in range(4):
			matrix_loss += self.lambda_for_mat_mag * (torch.norm(param[:, index]) - 1.5/8) ** 2
		return matrix_loss
	
	
	def _enc_st_sigmoid_forward(self, X_train):
		Y_predict = self.enc_st_sigmid(X_train)
		return Y_predict
	

		
	

	