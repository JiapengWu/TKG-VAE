import torch
import torch.nn as nn
import torch.nn.functional as F
import dgl.function as fn


class RGCNLayer(nn.Module):
    def __init__(self, in_feat, out_feat, bias=None, activation=None,
                 self_loop=True, dropout=0.0):
        super(RGCNLayer, self).__init__()
        self.bias = bias
        self.activation = activation
        self.self_loop = self_loop

        if self.bias == True:
            self.bias = nn.Parameter(torch.Tensor(out_feat))
            nn.init.xavier_uniform_(self.bias,
                                    gain=nn.init.calculate_gain('relu'))

        # weight for self loop
        if self.self_loop:
            self.loop_weight = nn.Parameter(torch.Tensor(in_feat, out_feat))
            nn.init.xavier_uniform_(self.loop_weight,
                                    gain=nn.init.calculate_gain('relu'))

        if dropout:
            self.dropout = nn.Dropout(dropout)
        else:
            self.dropout = None

    # define how propagation is done in subclass
    def propagate(self, g):
        raise NotImplementedError

    def forward(self, g):
        g = g.local_var()
        if self.self_loop:
            loop_message = torch.mm(g.ndata['h'], self.loop_weight)
            if self.dropout is not None:
                loop_message = self.dropout(loop_message)
        # import pdb; pdb.set_trace()
        self.propagate(g)

        # apply bias and activation
        node_repr = g.ndata['h']
        if self.bias:
            node_repr = node_repr + self.bias
        if self.self_loop:
            node_repr = node_repr + loop_message
        if self.activation:
            node_repr = self.activation(node_repr)

        g.ndata['h'] = node_repr
        return g

    def forward_isolated(self, ent_embeds):
        if self.self_loop:
            loop_message = torch.mm(ent_embeds, self.loop_weight)
            if self.dropout is not None:
                loop_message = self.dropout(loop_message)
            ent_embeds = ent_embeds + loop_message
        if self.bias:
            ent_embeds += self.bias
        if self.activation:
            ent_embeds = self.activation(ent_embeds)
        return ent_embeds


class RGCNBlockLayer(RGCNLayer):
    def __init__(self, in_feat, out_feat, num_rels, num_bases, bias=None,
                 activation=None, self_loop=False, dropout=0.0):
        super(RGCNBlockLayer, self).__init__(in_feat, out_feat, bias,
                                             activation, self_loop=self_loop,
                                             dropout=dropout)
        self.num_rels = num_rels
        self.num_bases = num_bases
        assert self.num_bases > 0

        self.out_feat = out_feat

        self.submat_in = in_feat // self.num_bases
        self.submat_out = out_feat // self.num_bases

        self.weight = nn.Parameter(torch.Tensor(self.num_rels, self.num_bases * self.submat_in * self.submat_out))
        nn.init.xavier_uniform_(self.weight, gain=nn.init.calculate_gain('relu'))

    def msg_func(self, edges):
        weight = self.weight.index_select(0, edges.data['type_s']).view(
                    -1, self.submat_in, self.submat_out)
        node = edges.src['h'].view(-1, 1, self.submat_in)
        msg = torch.bmm(node, weight).view(-1, self.out_feat)
        if 'norm' in edges.data:
            msg = msg * edges.data['norm']
        return {'msg': msg}

    def propagate(self, g):
        g.update_all(lambda x: self.msg_func(x), fn.sum(msg='msg', out='h'), self.apply_func)

    def apply_func(self, nodes):
        return {'h': nodes.data['h'] * nodes.data['norm']}


class RGCN(nn.Module):
    def __init__(self, args, hidden_size, embed_size, num_rels, static=False):
        super(RGCN, self).__init__()
        # in_feat = embed_size if static else hidden_size + embed_size
        self.layer_1 = RGCNBlockLayer(embed_size, hidden_size, 2 * num_rels, args.n_bases,
                   activation=None, self_loop=True, dropout=args.dropout)
        self.layer_2 = RGCNBlockLayer(hidden_size, hidden_size, 2 * num_rels, args.n_bases,
                   activation=F.relu, self_loop=True, dropout=args.dropout)

    def forward(self, batched_graph):
        batched_graph = self.layer_1(batched_graph)
        return self.layer_2(batched_graph)

    def forward_isolated(self, ent_embeds):
        return self.layer_2.forward_isolated(self.layer_1.forward_isolated(ent_embeds))

