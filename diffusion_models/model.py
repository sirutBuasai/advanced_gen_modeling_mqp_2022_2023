#An Implementation of Diffusion Network Model
#Oringinal source: https://github.com/acids-ircam/diffusion_models
# External libraries
import torch
import torch.nn as nn
import torch.nn.functional as F

class ConditionalLinear(nn.Module):
    """Conditional linear layer with specified input and output sizes
    """


    def __init__(self, num_in, num_out, n_steps):
        super(ConditionalLinear, self).__init__()
        self.num_out = num_out
        self.lin = nn.Linear(num_in, num_out)
        self.embed = nn.Embedding(n_steps, num_out)
        self.embed.weight.data.uniform_()


    def forward(self, x, y):
        out = self.lin(x)
        gamma = self.embed(y)
        out = gamma.view(-1, self.num_out) * out
        return out


class ConditionalModel(nn.Module):
    """Vanilla diffusion model for continuous data
    """

    def __init__(self, n_steps, input_size):
        super(ConditionalModel, self).__init__()
        self.lin1 = ConditionalLinear(input_size, 128, n_steps)
        self.lin2 = ConditionalLinear(128, 128, n_steps)
        self.lin3 = ConditionalLinear(128, 128, n_steps)
        self.lin4 = nn.Linear(128, input_size)


    def forward(self, x, y):
        x = F.softplus(self.lin1(x, y))
        x = F.softplus(self.lin2(x, y))
        x = F.softplus(self.lin3(x, y))
        return self.lin4(x)


class ConditionalTabularModel(nn.Module):
    """Tabular diffusion model with continuous and discrete features
    """


    def __init__(self, n_steps, hidden_size, continuous_size, discrete_size):
        super(ConditionalTabularModel, self).__init__()
        self.lin1 = ConditionalLinear(continuous_size + discrete_size, hidden_size, n_steps)
        self.lin2 = ConditionalLinear(hidden_size, hidden_size, n_steps)
        self.lin3 = ConditionalLinear(hidden_size, hidden_size, n_steps)
        # discrete
        self.lin_d1 = nn.Linear(hidden_size, hidden_size)
        self.relu_d = nn.ReLU()
        self.lin_d2 = nn.Linear(hidden_size, hidden_size)
        self.lin_d3 = nn.Linear(hidden_size, discrete_size)
        # continuous
        self.lin_c1 = nn.Linear(hidden_size, hidden_size)
        self.relu_c1 = nn.ReLU()
        self.lin_c2 = nn.Linear(hidden_size, hidden_size)
        self.relu_c2 = nn.ReLU()
        self.lin_c3 = nn.Linear(hidden_size, hidden_size)
        self.relu_c3 = nn.ReLU()
        self.lin_c4 = nn.Linear(hidden_size, hidden_size)
        self.lin_c5 = nn.Linear(hidden_size, continuous_size)


    def forward(self, x_c, x_d, y, feature_indices):
        x = torch.cat([x_c, x_d], dim=1)
        x = F.softplus(self.lin1(x, y))
        x = F.softplus(self.lin2(x, y))
        x = F.softplus(self.lin3(x, y))
        # discrete
        x_d = self.lin_d1(x)
        x_d = self.relu_d(x_d)
        x_d = self.lin_d2(x_d)
        x_d = self.lin_d3(x_d)
        results = []
        for class_index in feature_indices:
            start, end = class_index
            curr_class = F.softmax(x_d[:, start:end], dim=1)
            results.append(curr_class)
        x_d = torch.cat(results, dim=1)
        # continuous
        x_c = self.lin_c1(x)
        x_c = self.relu_c1(x_c)
        x_c = self.lin_c2(x_c)
        x_c = self.relu_c2(x_c)
        x_c = self.lin_c3(x_c)
        x_c = self.relu_c3(x_c)
        x_c = self.lin_c4(x_c)
        x_c = self.lin_c5(x_c)
        return x_c, x_d


class ConditionalMultinomialModel(nn.Module):
    """Multinomial diffusion model for discrete features
    """


    def __init__(self, n_steps, hidden_size, input_size):
        super(ConditionalMultinomialModel, self).__init__()
        self.lin1 = ConditionalLinear(input_size, hidden_size, n_steps)
        self.lin2 = ConditionalLinear(hidden_size, hidden_size, n_steps)
        self.lin3 = ConditionalLinear(hidden_size, hidden_size, n_steps)
        self.lin4 = nn.Linear(hidden_size, input_size)


    def forward(self, x, y, feature_indices):
        x = F.softplus(self.lin1(x, y))
        x = F.softplus(self.lin2(x, y))
        x = F.softplus(self.lin3(x, y))
        x = self.lin4(x)
        results = []
        for class_index in feature_indices:
            start, end = class_index
            results.append(F.softmax(x[:, start:end], dim=1))
        return torch.cat(results, dim=1)
