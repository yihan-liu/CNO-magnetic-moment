
import torch
from torch_geometric.data import Data, InMemoryDataset
import numpy as np
import pandas as pd


ATOM_TYPE_DICT = {
    'N': 0,
    'C': 1,
    'O': 2,
}

def atom_type_to_onehot(element: str):
    """
    Convert e.g. 'N1' -> 'N' -> one-hot vector [1,0,0,0]
    """
    if element not in ATOM_TYPE_DICT:
        return None

    one_hot = [0] * len(ATOM_TYPE_DICT)
    idx = ATOM_TYPE_DICT[element]
    one_hot[idx] = 1
    return one_hot

class AtomDataset(InMemoryDataset):
    def __init__(self, root, transform=None, pre_transform=None, threshold=2.0):
        """
        root: path containing the 'atoms.csv'
        cutoff: distance threshold for constructing edges
        """
        self.threshold = threshold
        super().__init__(root, transform, pre_transform)
        self.data, self.slices = torch.load(self.processed_paths[0], weights_only=False)

    @property
    def raw_file_names(self):
        return ['atoms.csv']
    
    @property
    def processed_file_names(self):
        return ['data.pt']
    
    def process(self):
        df = pd.read_csv(self.raw_paths[0])

        # 1) Collect node features
        #    - we'll store [one-hot(atom_type), x, y, z]
        node_features = []
        atom_labels = []  # for edges generation
        for _, row in df.iterrows():
            atom_label = row['ATOM']  # e.g. 'N1'
            atom_labels.append(atom_label[0])
            one_hot = atom_type_to_onehot(atom_label[0])  # encode the element (atom_label[0]) 
            if one_hot is None:
                continue  # skip atoms not in the dict
            # append coordinates
            coords = [row['X'], row['Y'], row['Z']]
            node_features.append(one_hot + coords)

        self.node_features = np.array(node_features)

        # Normalize atom locations (only use translation)
        self._normalize_atoms()  # update self.node_features

        x = torch.tensor(self.node_features, dtype=torch.float)  # shape (num_nodes, 7)

        # 2) Construct edge_index using distance cutoff
        self._generate_edges(df[['X', 'Y', 'Z']].values, atom_labels)  # generate self.edge_index

        # 3) Magnetic moment as labels (node-level)
        y = torch.tensor([df['MAGNETIC_MOMENT'].values[i] for i in range(len(df)) if df['ATOM'][i][0] in ATOM_TYPE_DICT], dtype=torch.float).view(-1, 1)

        data = Data(x=x, edge_index=self.edge_index, y=y)

        data_list = [data]
        self.data, self.slices = self.collate(data_list)
        torch.save((self.data, self.slices), self.processed_paths[0])
        
    def _normalize_atoms(self):
        """
        Translate the atoms so their center is at (0, 0, 0)
        """
        atoms = self.node_features[:, -3:]  # shape: (n, 3)
        center = np.mean(atoms, axis=0)
        translated_atoms = atoms - center

        self.node_features[:, -3:] = translated_atoms

    def _generate_edges(self, positions, atom_labels):
        """
        Build an edge index based on the following rules.
        - Carbon atoms (C) form bonds with up to three nearest atoms (C, N, O) within the threshold.
        - Nitrogen atoms (N) form bonds with up to three nearest carbon atoms within the threshold.
        
        Args:
        - positions: (num_nodes, 3) array of atomic positions
        - atom_labels: List of atom labels (e.g., ['C', 'N', 'O']).
        """
        num_nodes = positions.shape[0]
        edges = []

        for i in range(num_nodes):
            current_label = atom_labels[i]
            distances = []

            # Compute distances to all other atoms
            for j in range(num_nodes):
                if i == j:
                    continue
                dist = np.linalg.norm(positions[i] - positions[j])
                distances.append((dist, j))

            # Sort neighbors by distance
            distances.sort(key=lambda x: x[0])
            
            if current_label == 'C':
                # C forms bonds with up to 3 nearest atoms (C, N, O)
                bonded_atoms = 0
                for dist, j in distances:
                    if bonded_atoms >= 3:
                        break
                    if dist <= self.threshold and atom_labels[j] in {'C', 'N', 'O'}:
                        edges.append([i, j])
                        edges.append([j, i])
                        bonded_atoms += 1

            elif current_label == 'N':
                # N forms bonds with up to 3 nearest carbon atoms
                bonded_atoms = 0
                for dist, j in distances:
                    if bonded_atoms >= 3:
                        break
                    if dist <= self.threshold and atom_labels[j] == {'C'}:
                        edges.append([i, j])
                        edges.append([j, i])
                        bonded_atoms += 1
                        
        if len(edges) == 0:
            # No bonds are formed
            edges = [[0, 0]]

        self.edge_index = torch.tensor(edges, dtype=torch.long).t()  # Shape (2, E)

if __name__ == '__main__':
    dataset = AtomDataset(root='./', threshold=2.0)