#include "b2.h"
#include "b2_simple.h"
#include "b3.h"
#include "structure.h"
#include "four_body.h"
#include "normalized_dot_product.h"
#include "squared_exponential.h"
#include "structure.h"
#include "three_body.h"
#include "three_body_wide.h"
#include "two_body.h"
#include "gtest/gtest.h"
#include <Eigen/Dense>
#include <chrono>
#include <cmath>
#include <iostream>
#include <stdlib.h>

class StructureTest : public ::testing::Test {
public:
  int n_atoms = 10;
  int n_species = 3;
  Eigen::MatrixXd cell, cell_2, cell_3;
  std::vector<int> species, species_2, species_3;
  Eigen::MatrixXd positions, positions_2, positions_3;
  B2 ps;
  B2_Simple ps_simple;
  std::vector<Descriptor *> dc;
  Structure test_struc, test_struc_2, test_struc_3;
  DescriptorValues struc_desc;

  double cell_size = 10;
  double cutoff = cell_size / 2;
  int N = 3;
  int L = 3;
  std::string radial_string = "chebyshev";
  std::string cutoff_string = "cosine";
  std::vector<double> radial_hyps{0, cutoff};
  std::vector<double> cutoff_hyps;
  std::vector<int> descriptor_settings{n_species, N, L};
  int descriptor_index = 0;
  std::vector<double> many_body_cutoffs{cutoff};

  double sigma = 2.0;
  double ls = 0.9;
  int power = 1;
  NormalizedDotProduct kernel_3;
  SquaredExponential kernel;

  StructureTest() {
    // Make positions.
    cell = Eigen::MatrixXd::Identity(3, 3) * cell_size;
    cell_2 = Eigen::MatrixXd::Identity(3, 3) * cell_size;
    cell_3 = Eigen::MatrixXd::Identity(3, 3) * cell_size;

    positions = Eigen::MatrixXd::Random(n_atoms, 3) * cell_size / 2;
    positions_2 = Eigen::MatrixXd::Random(n_atoms, 3) * cell_size / 2;
    positions_3 = Eigen::MatrixXd::Random(n_atoms, 3) * cell_size / 2;

    // Make random species.
    for (int i = 0; i < n_atoms; i++) {
      species.push_back(rand() % n_species);
      species_2.push_back(rand() % n_species);
      species_3.push_back(rand() % n_species);
    }

    ps = B2(radial_string, cutoff_string, radial_hyps, cutoff_hyps,
            descriptor_settings);
    ps_simple = B2_Simple(radial_string, cutoff_string, radial_hyps,
                          cutoff_hyps, descriptor_settings);

    dc.push_back(&ps_simple);

    test_struc = Structure(cell, species, positions, cutoff, dc);
    test_struc_2 = Structure(cell_2, species_2, positions_2, cutoff, dc);
    test_struc_3 = Structure(cell_3, species_3, positions_3, cutoff, dc);

    struc_desc = test_struc.descriptors[0];

    kernel = SquaredExponential(sigma, ls);
    kernel_3 = NormalizedDotProduct(sigma, power);
  }
};
