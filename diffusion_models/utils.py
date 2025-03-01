# Loss Function for Diffusion Model
# Original Source: https://github.com/acids-ircam/diffusion_models
# Native libraries
import os

# External libraries
import numpy as np
import torch
import pandas as pd


def make_beta_schedule(schedule='linear', n_timesteps=1000, start=1e-5, end=1e-2):
    if schedule == "quad":
        betas = torch.linspace(start ** 0.5, end ** 0.5, n_timesteps) ** 2
    elif schedule == "sigmoid":
        betas = torch.linspace(-6, 6, n_timesteps)
        betas = torch.sigmoid(betas) * (end - start) + start
    else:
        # default linear beta schedule
        betas = torch.linspace(start, end, n_timesteps)

    return betas


def extract(input, t, x):
    """Extracts a single value from input at step t and reshapes using x.
    Used in the diffusion process

    Args:
        input (torch.Tensor): the input to extract from
        t (torch.Tensor): a tensor with a single element representing the time step to index the input
        x (torch.Tensor): the real data.  Only used for the shape
    """
    shape = x.shape
    out = torch.gather(input, 0, t.to(input.device))
    reshape = [t.shape[0]] + [1] * (len(shape) - 1)
    return out.reshape(*reshape)


def log_1_min_a(a):
    """Used for calculating categorical noise variables"""
    return torch.log(1 - a.exp() + 1e-40)


def q_posterior_mean_variance(x_0, x_t, t,posterior_mean_coef_1,posterior_mean_coef_2,posterior_log_variance_clipped):
    coef_1 = extract(posterior_mean_coef_1, t, x_0)
    coef_2 = extract(posterior_mean_coef_2, t, x_0)
    mean = coef_1 * x_0 + coef_2 * x_t
    var = extract(posterior_log_variance_clipped, t, x_0)
    return mean, var


def p_mean_variance(model, x, t):
    # Go through model
    out = model(x, t)
    # Extract the mean and variance
    mean, log_var = torch.split(out, 2, dim=-1)
    # var = torch.exp(log_var)
    return mean, log_var


def p_sample(model, x, t, alphas, betas, one_minus_alphas_bar_sqrt):
    t = torch.tensor([t])
    # Factor to the model output
    eps_factor = ((1 - extract(alphas, t, x)) / extract(one_minus_alphas_bar_sqrt, t, x))
    # Model output
    eps_theta = model(x, t)
    # Final values
    mean = (1 / extract(alphas, t, x).sqrt()) * (x - (eps_factor * eps_theta))
    # Generate z
    z = torch.randn_like(x)
    # Fixed sigma
    sigma_t = extract(betas, t, x).sqrt()
    sample = mean + sigma_t * z
    return (sample)


def p_sample_loop(model, shape,n_steps,alphas,betas,one_minus_alphas_bar_sqrt):
    """Removes noise from data one step at a time and appends each step into a list"""
    cur_x = torch.randn(shape)
    x_seq = [cur_x]
    for i in reversed(range(n_steps)):
        cur_x = p_sample(model, cur_x, i, alphas, betas, one_minus_alphas_bar_sqrt)
        x_seq.append(cur_x)
    return x_seq


def approx_standard_normal_cdf(x):
    return 0.5 * (1.0 + torch.tanh(torch.tensor(np.sqrt(2.0 / np.pi)) * (x + 0.044715 * torch.pow(x, 3))))


def discretized_gaussian_log_likelihood(x, means, log_scales):
    # Assumes data is integers [0, 255] rescaled to [-1, 1]
    centered_x = x - means
    inv_stdv = torch.exp(-log_scales)
    plus_in = inv_stdv * (centered_x + 1. / 255.)
    cdf_plus = approx_standard_normal_cdf(plus_in)
    min_in = inv_stdv * (centered_x - 1. / 255.)
    cdf_min = approx_standard_normal_cdf(min_in)
    log_cdf_plus = torch.log(torch.clamp(cdf_plus, min=1e-12))
    log_one_minus_cdf_min = torch.log(torch.clamp(1 - cdf_min, min=1e-12))
    cdf_delta = cdf_plus - cdf_min
    log_probs = torch.where(x < -0.999, log_cdf_plus, torch.where(x > 0.999, log_one_minus_cdf_min, torch.log(torch.clamp(cdf_delta, min=1e-12))))
    return log_probs


def normal_kl(mean1, logvar1, mean2, logvar2):
    """Calculates KL divergence for loss function"""
    kl = 0.5 * (-1.0 + logvar2 - logvar1 + torch.exp(logvar1 - logvar2) + ((mean1 - mean2) ** 2) * torch.exp(-logvar2))
    return kl


def q_sample(x_0, t, alphas_bar_sqrt, one_minus_alphas_bar_sqrt ,noise=None):
    """Samples q(t)"""
    if noise is None:
        noise = torch.randn_like(x_0)
    alphas_t = extract(alphas_bar_sqrt, t, x_0)
    alphas_1_m_t = extract(one_minus_alphas_bar_sqrt, t, x_0)
    return (alphas_t * x_0 + alphas_1_m_t * noise)


def loss_variational(model, x_0,alphas_bar_sqrt, one_minus_alphas_bar_sqrt,posterior_mean_coef_1,posterior_mean_coef_2,posterior_log_variance_clipped,n_steps):
    batch_size = x_0.shape[0]
    # Select a random step for each example
    t = torch.randint(0, n_steps, size=(batch_size // 2 + 1,))
    t = torch.cat([t, n_steps - t - 1], dim=0)[:batch_size].long()
    # Perform diffusion for step t
    x_t = q_sample(x_0, t, alphas_bar_sqrt, one_minus_alphas_bar_sqrt)
    # Compute the true mean and variance
    true_mean, true_var = q_posterior_mean_variance(x_0, x_t, t,posterior_mean_coef_1,posterior_mean_coef_2,posterior_log_variance_clipped)
    # Infer the mean and variance with our model
    model_mean, model_var = p_mean_variance(model, x_t, t)
    # Compute the KL loss
    kl = normal_kl(true_mean, true_var, model_mean, model_var)
    kl = torch.mean(kl.view(batch_size, -1), dim=1) / np.log(2.)
    # NLL of the decoder
    decoder_nll = -discretized_gaussian_log_likelihood(x_0, means=model_mean, log_scales=0.5 * model_var)
    decoder_nll = torch.mean(decoder_nll.view(batch_size, -1), dim=1) / np.log(2.)
    # At the first timestep return the decoder NLL, otherwise return KL(q(x_{t-1}|x_t,x_0) || p(x_{t-1}|x_t))
    output = torch.where(t == 0, decoder_nll, kl)
    return output.mean(-1)


def continuous_noise_estimation_loss(model, x_0_continuous, x_0_discrete, feature_indices, k, alphas_bar_sqrt, one_minus_alphas_bar_sqrt, n_steps):
    batch_size = x_0_continuous.shape[0]
    # Select a random step for each example
    t = torch.randint(0, n_steps, size=(batch_size // 2 + 1,))
    t = torch.cat([t, n_steps - t - 1], dim=0)[:batch_size].long()
    # x0 multiplier
    a = extract(alphas_bar_sqrt, t, x_0_continuous)
    # eps multiplier
    am1 = extract(one_minus_alphas_bar_sqrt, t, x_0_continuous)
    # Get noise for input
    e = torch.randn_like(x_0_continuous)
    weights = torch.Tensor([1]) / k
    weights = weights.repeat(k)
    c = torch.multinomial(weights, x_0_discrete.shape[0], replacement=True)
    c = torch.nn.functional.one_hot(c, k).float()
    # model input
    x = x_0_continuous * a + e * am1
    output, _ = model(x, c, t, feature_indices)
    return (e - output).square().mean()


def categorical_noise_estimation_loss(model, x_0_continuous, x_0_discrete, diffs, k, feature_indices):
    """Calculates the loss in estimating the noise of x_t

    Args:
        model (ConditionalTabularModel): the model
        x_0 (torch.Tensor): the original categorical data at t=0
        x_0_continuous (torch.Tensor): the continuous data, used to make gaussian noise for the
        diffs (Diffusion): the class encapsulating the diffusion variables
        k (int): the total number of classes across all features
        feature_indices (list<tuples>): a list of the indices for all the features

    NOTES:
        x_0: (batch_size, k) for one feature

        Data should be (128, n*k_n), where n is number of features and k is number of classes in each feature

    """
    n_steps = diffs.num_steps
    batch_size = x_0_discrete.shape[0]

    # Select a random step for each example
    t = torch.randint(0, n_steps, size=(batch_size // 2 + 1,))
    t = torch.cat([t, n_steps - t - 1], dim=0)[:batch_size].long()

    # Get t-1 and ensure values are not negative
    t_1 = t - 1
    t_1[t_1 == -1] = 0

    # Get x_t for each time step in feature
    batch_x_list = []
    for index in feature_indices:
        feature = x_0_discrete[:, index[0]:index[1]]
        k_x = index[1] - index[0]
        batch_feature = q_x_cat(feature, diffs, t, k_x)
        batch_x_list.append(batch_feature)
    batch_x_t = torch.cat(batch_x_list, dim=1)

    # Extract values for loss
    alpha = extract(diffs.alphas, t, x_0_discrete)
    one_minus_alpha = 1 - alpha
    alphas_prod = extract(diffs.alphas_prod, t_1, x_0_discrete)
    one_minus_alpha_prod = 1 - alphas_prod

    # Calculate theta (expected value)
    theta = (alpha * batch_x_t + one_minus_alpha / k) * (alphas_prod * x_0_discrete + one_minus_alpha_prod / k)

    # Normalize each feature at every time step so it sums to one
    feature_normalization = []
    for index in feature_indices:
        feature_normalization.append(torch.nn.functional.normalize(theta[:, index[0]:index[1]], p=1, dim=1))
    theta = torch.cat(feature_normalization, dim=1)

    # Get random noise for model
    weights = torch.tensor([1/k]).repeat(k)
    e = torch.multinomial(weights, x_0_discrete.shape[0], replacement=True)
    e = torch.nn.functional.one_hot(e, k).float()
    g = torch.randn(x_0_continuous.shape)

    # Get model output from noise and compare with theta
    _, output = model(g, e, t, feature_indices)
    theta = theta.squeeze(1)

    return (theta - output).square().mean()


def q_x_cat(x_0, diffs, t, k):
    """Function to add t time steps of noise to discrete data x

    Args:
        x_0 (torch.Tensor): the discrete data to add noise to
        diffs (class: Diffusion): a diffusion model class encapsulating proper constants for forward diffusion
                                Constants calculated from num_steps input to class constructor
        t (torch.Tensor): the number of noise steps to add
        k (int): the number of classes for the feature

    Returns:
        (torch.Tensor): the data with the noise added to it
    """
    probs = get_probs(x_0)       # torch.Size([k])
    cumprod_alpha = extract_cat(diffs.alphas_prod, t, x_0.shape)
    cumprod_1_minus_alpha = extract_cat(diffs.one_minus_alphas_bar_sqrt, t, x_0.shape)
    x_t_probs = cumprod_alpha*probs + cumprod_1_minus_alpha / k
    x_t = resample(x_t_probs)

    return torch.nn.functional.one_hot(x_t, k)


def extract_cat(a, t, x_shape):
    b, *_ = t.shape
    out = a.gather(-1, t)
    return out.reshape(b, *((1,) * (len(x_shape) - 1)))


def log_add_exp(a, b):
    maximum = torch.max(a, b)
    return maximum + torch.log(torch.exp(a - maximum) + torch.exp(b - maximum))


def resample(distribution):
    """Resamples from a probability distribution

    Args:
        distribution (torch.Tensor): 2D tensor with second dimension with the probabilties
    """
    return torch.multinomial(distribution, num_samples=1, replacement=True).squeeze(dim=1)


def get_probs(data):
    """Calculate probablity distribution for given data with K classes

    Args:
        data (torch.Tensor): a 2D tensor of data with the second dimension being the one-hot encodings
        K (int): number of classes

    Returns:
        (torch.Tensor): a 2D tensor of probabilities
    """
    sums = data.sum(dim=0)
    totals = sums.sum(dim=0).unsqueeze(dim=-1)
    return (sums / totals)


def get_classes(data):
    """Finds all the classes in the data

    Returns:
        (torch.Tensor): a tensor of all the classes

    Ex: data = torch.tensor([0, 0, 1, 0, 2])
        get_classes(data)
        --> tensor([0, 1, 2])
    """
    return data.unique(return_counts=True)[0]


def normalize(probs):
    """Normalizes distribution to add up to one"""
    sum = torch.sum(probs)
    return probs / sum


def to_one_hot(data, feature_indices):
    """Makes one hot encoding of data for each discrete features"""
    one_hot_list = []
    for i, class_index in enumerate(feature_indices):
        start, end = class_index
        feature = data[:, i]
        one_hot_list.append(torch.nn.functional.one_hot(feature.long(), end - start))
    one_hot = torch.cat(one_hot_list, dim=1)
    return one_hot


def get_model_output(model, input_size, diffusion, num_to_gen):
    """Gets the output of the model

    Args:
        model (ConditionalModel): the model to be used
        input_size (int): the number of dimensions of the dataset
        diffusion (Diffusion): the class holding the denoising variables
        num_to_gen (int): number of samples to generate
    """
    with torch.no_grad():
        x_seq = p_sample_loop(model, torch.Size([num_to_gen, input_size]), diffusion.num_steps, diffusion.alphas, diffusion.betas, diffusion.one_minus_alphas_bar_sqrt)
    output = x_seq[-1]

    return output


def p_tabular_sample(model, x, e, t, feature_indices, alphas, betas, one_minus_alphas_bar_sqrt):
    t = torch.tensor([t])
    # Factor to the model output
    eps_factor = ((1 - extract(alphas, t, x)) / extract(one_minus_alphas_bar_sqrt, t, x))
    # Model output
    eps_theta,_ = model(x, e, t, feature_indices)
    # Final values
    mean = (1 / extract(alphas, t, x).sqrt()) * (x - (eps_factor * eps_theta))
    # Generate z
    z = torch.randn_like(x)
    # Fixed sigma
    sigma_t = extract(betas, t, x).sqrt()
    sample = mean + sigma_t * z
    return sample


def p_tabular_sample_loop(model, e, shape, feature_indices, n_steps, alphas, betas, one_minus_alphas_bar_sqrt):
    """Removes noise from data one step at a time and appends each step into a list"""
    curr_x = torch.randn(shape)
    x_seq = [curr_x]
    for i in reversed(range(n_steps)):
        curr_x = p_tabular_sample(model, curr_x, e, i, feature_indices, alphas, betas, one_minus_alphas_bar_sqrt)
        x_seq.append(curr_x)
    noise_removed = x_seq[-1]
    return noise_removed


def get_discrete_model_output(model, k, num_to_gen, feature_indices, continuous):
    """Gets the output of a discrete model

    Returns:
        continuous_output (torch.Tensor): the generated data
        discrete_output (torch.Tensor): a probability tensor of size n*k
    """
    t = torch.Tensor([0]).repeat(num_to_gen).int()
    weights = torch.Tensor([1]) / k
    weights = weights.repeat(k)
    e = torch.multinomial(weights, num_to_gen, replacement=True)
    e = torch.nn.functional.one_hot(e, k).float()
    g = torch.randn((num_to_gen, continuous.shape[1]))
    with torch.no_grad():
        continuous_output, discrete_output = model(g, e, t, feature_indices)

    return continuous_output, discrete_output[0]


def get_tabular_model_output(model, k, sample_size, feature_indices, num_continuous_feature, diffusion, calculate_continuous=False):
    """Gets the output of the tabular model

    Args:
        model (ConditionalTabularModel): the tabular diffusion model for reverse diffusion
        k (int): number of discrete classes
        sample_size (intc): number of sample size to generate
        feature_indices (list<tuples>): a list of the indices for all the features
        num_continuous_feature (int): number of continuous features
        diffusion (Diffusion): a diffusion model class encapsulating proper constants for forward diffusion
        calculate_continuous (bool): True if continuous output is wanted

    Returns:
        continuous_output (torch.Tensor): the generated data
        discrete_output (torch.Tensor): a probability tensor of size n*k
    """
    # continuous output
    t = torch.Tensor([0]).repeat(sample_size).int()
    weights = torch.Tensor([1]) / k
    weights = weights.repeat(k)
    e = torch.multinomial(weights, sample_size, replacement=True)
    e = torch.nn.functional.one_hot(e, k).float()
    g = torch.randn((sample_size, num_continuous_feature))

    with torch.no_grad():
        continuous_output = 1
        if calculate_continuous:
            continuous_output = p_tabular_sample_loop(model, e, torch.Size([sample_size, num_continuous_feature]), feature_indices, diffusion.num_steps, diffusion.alphas, diffusion.betas, diffusion.one_minus_alphas_bar_sqrt)

        # discrete output
        _, discrete_distribution = model(g, e, t, feature_indices)
        discreate_features = []
        for start, end in feature_indices:
            discreate_features.append(resample(discrete_distribution[:,start:end]).unsqueeze(1))
        discrete_output = torch.cat(discreate_features, 1)

    return continuous_output, discrete_output, discrete_distribution[0]


def load_data(dataset, dataset_type):
    """Load data from text file

    Load data from a given dataset name and dataset type (train/test).
    The function expects the data to be in the following format:
    "{dataset}/{dataset_type}/(X|y)_{dataset_type}.txt"

    Args:
        dataset (string): the name of the directory that the data lives in.
        dataset_type (string): train or test type

    Returns:
        data (torch.Tensor): the features of the data.
        labels (torch.Tensor): the labels of the data.
    """
    # load data and its labels
    x = np.loadtxt(os.path.join(dataset, dataset_type, f"X_{dataset_type}.txt"))
    y = np.loadtxt(os.path.join(dataset, dataset_type, f"y_{dataset_type}.txt"))

    # convert loaded data from numpy to tensor
    data = torch.from_numpy(x).float()
    labels = torch.from_numpy(y).float()

    # convert 1-indexed class labels to 0-indexed labels
    labels -= 1

    return data, labels


def get_activity_data(x, y, activity_label):
    """Parse through data set to get a specified activity

    Given data x, y, and an activity label, return a subset of the data with only specified label.
    Activity label is defined as the following:
        WALKING: 0
        WALKING_UPSTAIRS: 1
        WALKING_DOWNSTAIRS: 2
        SITTING: 3
        STANDING: 4
        LAYING: 5

    Args:
        x (torch.Tensor): the features of the data.
        y (torch.Tensor): the labels of the data.
        activity_label (int): specify the activity label wanted.

    Returns:
        data_x (torch.Tensor): the features of the data given the activity label.
        data_y (torch.Tensor): the labels of the data given the activity label.
    """
    # find a list of index in y where label is equal to the specified activity label
    activity_idx = (y == activity_label).nonzero().flatten()
    # make data_x and data_y tensor with data from the specified activity label
    data_x = x[activity_idx,:]
    data_y = torch.multiply(torch.ones(data_x.size(0)), activity_label)

    return data_x, data_y


def read_user_data(uid):
    """Reads a user data from the ExtraSensory dataset given a user ID

    Assumes the current folder/file structure does not change
    Example UID: '1155FF54-63D3-4AB2-9863-8385D0BD0A13'

    Args:
        uid (String): the user ID of the user to get the data for

    Returns:
        df (pandas.DataFrame): the dataframe of the user's data
        feature_names (pandas.DataFrame): the data for all of the features
        labels (pandas.DataFrame): the data for all of the labels
    """
    df = pd.read_csv(f'./../dataset/ExtraSensory/{uid}.features_labels.csv/{uid}.features_labels.csv')
    feature_names = df.iloc[:, 0:226]
    labels = df.iloc[:, 226:]
    return df, feature_names, labels


def separate_tabular_data(data, features):
    """Retrieves the discrete and continuous features from a dataset

    Args:
        data (torch.Tensor): the data to split
        features (list<strings>): a list of the feature names of the columns for the data

    Returns:
        continuous (torch.Tensor): the continuous data
        discrete (torch.Tensor): the discrete data
    """
    continuous_indices = []
    discrete_indices = []
    for i, name in enumerate(features):
        if name.__contains__('discrete'):
            discrete_indices.append(i)
        else:
            continuous_indices.append(i)

    discrete = torch.index_select(data, 1, torch.tensor(discrete_indices))
    continuous = torch.index_select(data, 1, torch.tensor(continuous_indices))

    return continuous, discrete
