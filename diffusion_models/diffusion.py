# Code from https://github.com/azad-academy/denoising-diffusion-model/blob/main/diffusion_model_demo.ipynb
# Internal libraries
import utils
from early_stopper import EarlyStopper
from ema import EMA
from model import ConditionalTabularModel

# External libraries
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import torch
import torch.optim as optim
from torch.nn.utils import clip_grad


class Diffusion():
    """Diffusion model class
    """


    def __init__(self, num_steps):
        """
        Class constructor for feed-forward model.

        All fields of model are torch.Tensor's of size(num_steps)

        Args:
            num_steps (int): number of time steps desired in diffusion model
        """
        super().__init__()
        self.num_steps = num_steps
        # For continuous noise
        # Beta scheduler
        self.betas = utils.make_beta_schedule(schedule='linear', n_timesteps=num_steps, start=1e-5, end=0.5e-2)
        # Alphas
        self.alphas = 1 - self.betas
        # Cumulative product of alphas
        self.alphas_prod = torch.cumprod(self.alphas, 0)
        # Square root of the cumulative product of alphas
        self.alphas_bar_sqrt = torch.sqrt(self.alphas_prod)
        # One minus the cumulative product of alphas
        self.one_minus_alphas_bar_sqrt = torch.sqrt(1 - self.alphas_prod)

        # For categorical noise
        # Log of alphas
        self.log_alphas = np.log(self.alphas)
        # One minus log of alphas
        self.one_minus_log_alphas = utils.log_1_min_a(self.log_alphas)
        # Cumulative sum of log of alphas
        self.log_cumprod_alpha = np.cumsum(self.log_alphas)
        # One minus log of cumulative sum
        self.log_1_min_cumprod_alpha = utils.log_1_min_a(self.log_cumprod_alpha)


def get_denoising_variables(num_steps):
    """Calculates the variables used in the denoising process and captures them in the class 'Diffusion"

    Args:
        num_steps (int): the number of steps in the forward diffusion

    Returns:
        diffusion (Diffusion): a class encapsulating the denoising variables
    """
    diffusion = Diffusion(num_steps)

    return diffusion


def q_x(x_0, t, model, noise=None):
    """Function to add t time steps of noise to continuous data x

    Args:
        x_0 (torch.Tensor): the data to add noise to
        t (torch.Tensor): the number of time steps to add
        model (class: Diffusion): a diffusion model class encapsulating proper constants for forward diffusion
                                Constants calculated from num_steps input to class constructor

    Returns:
        (torch.Tensor): the data with the noise added to it
    """
    if noise is None:
        noise = torch.randn_like(x_0)
    alphas_t = utils.extract(model.alphas_bar_sqrt, t, x_0)
    alphas_1_m_t = utils.extract(model.one_minus_alphas_bar_sqrt, t, x_0)
    return (alphas_t * x_0 + alphas_1_m_t * noise)


def visualize_forward(dataset, num_steps, num_divs, diffusion):
    """Vizualizes the forward diffusion process

    Args:
        dataset (torch.Tensor): the original dataset without noise
        num_steps (int): number of steps of noise to be removed
        num_divs (int): number of graphs to plot
        diffusion (class: Diffusion): a diffusion class which captures forward diffusion variables
    """
    _, axs = plt.subplots(1, num_divs + 1, figsize=(28, 3))
    axs[0].scatter(dataset[:, 0], dataset[:, 1],color='white',edgecolor='gray', s=5)
    axs[0].set_axis_off()
    axs[0].set_title('$q(\\mathbf{x}_{'+str(0)+'})$')
    for i in range(1, num_divs + 1):
        # q_i = q_x(dataset, torch.tensor([i * int(num_steps/num_divs) - 1]), diffusion)
        q_i = utils.q_x_cat(dataset, torch.tensor([i * int(num_steps/num_divs) - 1]), diffusion, torch.tensor([2]))
        axs[i].scatter(q_i[:, 0], q_i[:, 1],color='white',edgecolor='gray', s=5)
        axs[i].set_axis_off()
        axs[i].set_title('$q(\\mathbf{x}_{'+str(i*int(num_steps/num_divs))+'})$')
    plt.show()


def visualize_backward(model, dataset, num_steps, num_divs, diffusion, heatmap=False, reverse=False):
    """Vizualizes the backwards diffusion process

    Args:
        model (class: ConditionalModel): the model being used
        dataset (torch.Tensor): the original dataset without noise
        num_steps (int): number of steps of noise to be removed
        num_divs (int): number of graphs to plot
        diffusion (class: Diffusion): a diffusion class which captures forward diffusion variables
        reverse (bool): If true, will plot the graphs in reverse
    """
    x_seq = utils.p_sample_loop(model, dataset.shape,num_steps,diffusion.alphas,diffusion.betas,diffusion.one_minus_alphas_bar_sqrt)
    _, axs = plt.subplots(2, num_divs+1, figsize=(28, 6))
    for i in range(num_divs + 1):
        cur_x = x_seq[i * int(num_steps/num_divs)].detach()
        axs[0, i if not reverse else num_divs-i].scatter(cur_x[:, 0], cur_x[:, 1],color='white',edgecolor='gray', s=5)
        axs[0, i if not reverse else num_divs-i].set_axis_off()
        axs[0, i if not reverse else num_divs-i].set_title('$q(\\mathbf{x}_{'+str(int((num_divs-i)*(num_steps)/num_divs))+'})$')

        if heatmap:
            cur_df = pd.DataFrame(cur_x)
            sns.kdeplot(data=cur_df, x=0, y=1, fill=True, thresh=0, levels=100, ax=axs[1, i if not reverse else num_divs-i], cmap="mako")


def forward_diffusion(dataset, num_steps, plot=False, num_divs=10):
    """Applies forward diffusion to the dataset with the given number of steps

    Args:
        dataset (torch.Tensor): the dataset to be diffused
        num_steps (int): the step size for the addition of noise to the data
        plot (bool): true if you want to plot the data with the noise added
        num_divs (int): number of plots to make. Only applicable if plot=True
    """
    diffusion = Diffusion(num_steps)

    if plot:
        # Visualize the forward process
        visualize_forward(dataset, num_steps, num_divs, diffusion)

    return diffusion


def use_model(model, dataset, diffusion, t):
    """Takes in a trained diffusion model and creates n datapoints from Gaussian noise

    Args:
        model (ConditionalModel): a trained diffusion model
        dataset (torch.Tensor): the dataset shape to model from
        diffusion (Diffusion): the diffusion variables for the denoising process
        t (int): the number of time steps to remove from the noise

    Returns:
        data (torch.Tensor): the generated data
    """
    output = utils.p_sample(model, dataset, t, diffusion.alphas, diffusion.betas, diffusion.one_minus_alphas_bar_sqrt)
    return output


def reverse_tabular_diffusion(discrete_tr, continuous_tr, discrete_vl, continuous_vl, diffusion, k, feature_indices,
                              batch_size=128, optim_lr=1e-3,
                              continuous_lr=1., discrete_lr=1.,
                              training_time_steps=None,
                              model=None, show_loss=True,
                              es_patience=500, es_delta=0.3):
    """Applies reverse diffusion to a dataset

    Args:
        discrete_tr (torch.Tensor): the discrete features for training
        continuous_tr (torch.Tensor): the continuous features for training
        discrete_vl (torch.Tensor): the discrete features for validation
        continuous_vl (torch.Tensor): the continuous features for validation
        diffusion (Diffusion): a diffusion model class encapsulating proper constants for forward diffusion
        k (int): number of total classes across all features
        feature_indices (list<tuples>): a list of the indices for all the features
        batch_size (int): number of batch sizes in each reverse diffusion step
        lr (float): learning rate for the reverse diffusion
        training_time_steps (int): number of training steps to remove noise.  Default is step_size from diffusion class
        model (ConditionalModel): optional argument to train a previously defined model
        show_loss (bool): default True, print loss statistics
        es_patience (int): early stopping patience -- number of iterations where validation loss does not decrease before early stopping training
        es_delta (float): early stopping delta -- minimum change in the validation loss to qualify as an improvement

    Returns:
        model (ConditionalModel): the trained model
        loss_list (list<float>): the list of losses from the training
        discrete_distribution_list (list<torch.Tensor>): the list of discrete distribution
    """
    # Load variables from diffusion class
    training_loss, validation_loss = None, None
    num_steps = diffusion.num_steps
    alphas_bar_sqrt = diffusion.alphas_bar_sqrt
    one_minus_alphas_bar_sqrt = diffusion.one_minus_alphas_bar_sqrt

    if training_time_steps is None:
        training_time_steps = num_steps

    # If no model given, create new one
    if model is None:
        hidden_size = 128
        model = ConditionalTabularModel(num_steps, hidden_size, continuous_tr.shape[1], k)

    optimizer = optim.Adam(model.parameters(), lr=optim_lr)

    # Create EMA model
    ema = EMA(0.9)
    ema.register(model)

    # Create an Early Stopper
    es = EarlyStopper(patience=es_patience, min_delta=es_delta)

    # Only tracked for graphing loss afterwards
    training_loss_list = []
    validation_loss_list = []
    discrete_distribution_list = []

    for t in range(training_time_steps):
        tr_multinomial_loss, tr_continuous_loss = 0, 0
        vl_multinomial_loss, vl_continuous_loss = 0, 0
        permutation_discrete = torch.randperm(discrete_tr.shape[0])
        permutation_continuous = torch.randperm(continuous_tr.shape[0])
        for i in range(0, continuous_tr.shape[0], batch_size):
            # Put model in training mode
            model.train()
            # Retrieve current batch
            indices_discrete = permutation_discrete[i:i+batch_size]
            indices_continuous = permutation_continuous[i:i+batch_size]
            batch_x_discrete = discrete_tr[indices_discrete]
            batch_x_continuous = continuous_tr[indices_continuous]
            # One hot encoding
            batch_x_discrete = utils.to_one_hot(batch_x_discrete, feature_indices)
            # Compute the loss
            tr_multinomial_loss = utils.categorical_noise_estimation_loss(model,
                                                                          batch_x_continuous,
                                                                          batch_x_discrete,
                                                                          diffusion,
                                                                          k,
                                                                          feature_indices) * discrete_lr
            tr_continuous_loss = utils.continuous_noise_estimation_loss(model,
                                                                        batch_x_continuous,
                                                                        batch_x_discrete,
                                                                        feature_indices,
                                                                        k,
                                                                        alphas_bar_sqrt,
                                                                        one_minus_alphas_bar_sqrt,
                                                                        num_steps) * continuous_lr
            training_loss = tr_multinomial_loss + tr_continuous_loss
            # Before the backward pass, zero all of the network gradients
            optimizer.zero_grad()
            # Backward pass: compute gradient of the loss with respect to parameters
            training_loss.backward()
            # Perform gradient clipping
            clip_grad.clip_grad_norm_(model.parameters(), 1.)
            # Calling the step function to update the parameters
            optimizer.step()
            # Update the exponential moving average
            ema.update(model)

        # Put model in evaluation mode to get validation loss
        model.eval()
        # Compute validation loss
        discrete_vl = utils.to_one_hot(discrete_vl, feature_indices)
        vl_multinomial_loss = utils.categorical_noise_estimation_loss(model,
                                                                      continuous_vl,
                                                                      discrete_vl,
                                                                      diffusion,
                                                                      k,
                                                                      feature_indices) * discrete_lr
        vl_continuous_loss = utils.continuous_noise_estimation_loss(model,
                                                                    continuous_vl,
                                                                    discrete_vl,
                                                                    feature_indices,
                                                                    k,
                                                                    alphas_bar_sqrt,
                                                                    one_minus_alphas_bar_sqrt,
                                                                    num_steps) * continuous_lr
        validation_loss = vl_multinomial_loss + vl_continuous_loss

        # get discrete distribution
        _,_, discrete_distribution = utils.get_tabular_model_output(model, k, 1000, feature_indices, continuous_tr.shape[1], diffusion, calculate_continuous=False)
        discrete_distribution_list.append(discrete_distribution.squeeze(0))

        # Add training loss statistics
        if training_loss and validation_loss and tr_continuous_loss and tr_multinomial_loss and vl_continuous_loss and vl_multinomial_loss:
            if show_loss and t % (training_time_steps // 10) == 0:
                # Print losses statistics
                print(f'Training Steps: {t}\n\t'
                      f'Training Loss: {round(training_loss.item(), 8)}\t'
                      f'Training Continuous Loss: {round(tr_continuous_loss.item(), 8)}\t'
                      f'Training Discrete Loss: {round(tr_multinomial_loss.item(), 8)}\n\t'
                      f'Validation Loss: {round(validation_loss.item(), 8)}\t'
                      f'Validation Continuous Loss: {round(vl_continuous_loss.item(), 8)}\t'
                      f'Validation Discrete Loss: {round(vl_multinomial_loss.item(), 8)}\n')

            # Early stopping if validation loss no longer decreases
            if es(validation_loss.item(), debug=True):
                # Print losses statistics
                print(f'Early Stopped at Training Step: {t}\n\t'
                      f'Training Loss: {round(training_loss.item(), 8)}\t'
                      f'Training Continuous Loss: {round(tr_continuous_loss.item(), 8)}\t'
                      f'Training Discrete Loss: {round(tr_multinomial_loss.item(), 8)}\n\t'
                      f'Validation Loss: {round(validation_loss.item(), 8)}\t'
                      f'Validation Continuous Loss: {round(vl_continuous_loss.item(), 8)}\t'
                      f'Validation Discrete Loss: {round(vl_multinomial_loss.item(), 8)}\n')
                break

            # Add losses to global lists
            training_loss_list.append(training_loss.item())
            validation_loss_list.append(validation_loss.item())


    return model, training_loss_list, validation_loss_list, discrete_distribution_list
