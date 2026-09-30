#include "car.h"

namespace {
#define DIM 9
#define EDIM 9
#define MEDIM 9
typedef void (*Hfun)(double *, double *, double *);

double mass;

void set_mass(double x){ mass = x;}

double rotational_inertia;

void set_rotational_inertia(double x){ rotational_inertia = x;}

double center_to_front;

void set_center_to_front(double x){ center_to_front = x;}

double center_to_rear;

void set_center_to_rear(double x){ center_to_rear = x;}

double stiffness_front;

void set_stiffness_front(double x){ stiffness_front = x;}

double stiffness_rear;

void set_stiffness_rear(double x){ stiffness_rear = x;}
const static double MAHA_THRESH_25 = 3.8414588206941227;
const static double MAHA_THRESH_24 = 5.991464547107981;
const static double MAHA_THRESH_30 = 3.8414588206941227;
const static double MAHA_THRESH_26 = 3.8414588206941227;
const static double MAHA_THRESH_27 = 3.8414588206941227;
const static double MAHA_THRESH_29 = 3.8414588206941227;
const static double MAHA_THRESH_28 = 3.8414588206941227;
const static double MAHA_THRESH_31 = 3.8414588206941227;

/******************************************************************************
 *                      Code generated with SymPy 1.14.0                      *
 *                                                                            *
 *              See http://www.sympy.org/ for more information.               *
 *                                                                            *
 *                         This file is part of 'ekf'                         *
 ******************************************************************************/
void err_fun(double *nom_x, double *delta_x, double *out_4251681860653429657) {
   out_4251681860653429657[0] = delta_x[0] + nom_x[0];
   out_4251681860653429657[1] = delta_x[1] + nom_x[1];
   out_4251681860653429657[2] = delta_x[2] + nom_x[2];
   out_4251681860653429657[3] = delta_x[3] + nom_x[3];
   out_4251681860653429657[4] = delta_x[4] + nom_x[4];
   out_4251681860653429657[5] = delta_x[5] + nom_x[5];
   out_4251681860653429657[6] = delta_x[6] + nom_x[6];
   out_4251681860653429657[7] = delta_x[7] + nom_x[7];
   out_4251681860653429657[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_4799215824192635798) {
   out_4799215824192635798[0] = -nom_x[0] + true_x[0];
   out_4799215824192635798[1] = -nom_x[1] + true_x[1];
   out_4799215824192635798[2] = -nom_x[2] + true_x[2];
   out_4799215824192635798[3] = -nom_x[3] + true_x[3];
   out_4799215824192635798[4] = -nom_x[4] + true_x[4];
   out_4799215824192635798[5] = -nom_x[5] + true_x[5];
   out_4799215824192635798[6] = -nom_x[6] + true_x[6];
   out_4799215824192635798[7] = -nom_x[7] + true_x[7];
   out_4799215824192635798[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_318226151794054394) {
   out_318226151794054394[0] = 1.0;
   out_318226151794054394[1] = 0.0;
   out_318226151794054394[2] = 0.0;
   out_318226151794054394[3] = 0.0;
   out_318226151794054394[4] = 0.0;
   out_318226151794054394[5] = 0.0;
   out_318226151794054394[6] = 0.0;
   out_318226151794054394[7] = 0.0;
   out_318226151794054394[8] = 0.0;
   out_318226151794054394[9] = 0.0;
   out_318226151794054394[10] = 1.0;
   out_318226151794054394[11] = 0.0;
   out_318226151794054394[12] = 0.0;
   out_318226151794054394[13] = 0.0;
   out_318226151794054394[14] = 0.0;
   out_318226151794054394[15] = 0.0;
   out_318226151794054394[16] = 0.0;
   out_318226151794054394[17] = 0.0;
   out_318226151794054394[18] = 0.0;
   out_318226151794054394[19] = 0.0;
   out_318226151794054394[20] = 1.0;
   out_318226151794054394[21] = 0.0;
   out_318226151794054394[22] = 0.0;
   out_318226151794054394[23] = 0.0;
   out_318226151794054394[24] = 0.0;
   out_318226151794054394[25] = 0.0;
   out_318226151794054394[26] = 0.0;
   out_318226151794054394[27] = 0.0;
   out_318226151794054394[28] = 0.0;
   out_318226151794054394[29] = 0.0;
   out_318226151794054394[30] = 1.0;
   out_318226151794054394[31] = 0.0;
   out_318226151794054394[32] = 0.0;
   out_318226151794054394[33] = 0.0;
   out_318226151794054394[34] = 0.0;
   out_318226151794054394[35] = 0.0;
   out_318226151794054394[36] = 0.0;
   out_318226151794054394[37] = 0.0;
   out_318226151794054394[38] = 0.0;
   out_318226151794054394[39] = 0.0;
   out_318226151794054394[40] = 1.0;
   out_318226151794054394[41] = 0.0;
   out_318226151794054394[42] = 0.0;
   out_318226151794054394[43] = 0.0;
   out_318226151794054394[44] = 0.0;
   out_318226151794054394[45] = 0.0;
   out_318226151794054394[46] = 0.0;
   out_318226151794054394[47] = 0.0;
   out_318226151794054394[48] = 0.0;
   out_318226151794054394[49] = 0.0;
   out_318226151794054394[50] = 1.0;
   out_318226151794054394[51] = 0.0;
   out_318226151794054394[52] = 0.0;
   out_318226151794054394[53] = 0.0;
   out_318226151794054394[54] = 0.0;
   out_318226151794054394[55] = 0.0;
   out_318226151794054394[56] = 0.0;
   out_318226151794054394[57] = 0.0;
   out_318226151794054394[58] = 0.0;
   out_318226151794054394[59] = 0.0;
   out_318226151794054394[60] = 1.0;
   out_318226151794054394[61] = 0.0;
   out_318226151794054394[62] = 0.0;
   out_318226151794054394[63] = 0.0;
   out_318226151794054394[64] = 0.0;
   out_318226151794054394[65] = 0.0;
   out_318226151794054394[66] = 0.0;
   out_318226151794054394[67] = 0.0;
   out_318226151794054394[68] = 0.0;
   out_318226151794054394[69] = 0.0;
   out_318226151794054394[70] = 1.0;
   out_318226151794054394[71] = 0.0;
   out_318226151794054394[72] = 0.0;
   out_318226151794054394[73] = 0.0;
   out_318226151794054394[74] = 0.0;
   out_318226151794054394[75] = 0.0;
   out_318226151794054394[76] = 0.0;
   out_318226151794054394[77] = 0.0;
   out_318226151794054394[78] = 0.0;
   out_318226151794054394[79] = 0.0;
   out_318226151794054394[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_6040091135663911588) {
   out_6040091135663911588[0] = state[0];
   out_6040091135663911588[1] = state[1];
   out_6040091135663911588[2] = state[2];
   out_6040091135663911588[3] = state[3];
   out_6040091135663911588[4] = state[4];
   out_6040091135663911588[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_6040091135663911588[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_6040091135663911588[7] = state[7];
   out_6040091135663911588[8] = state[8];
}
void F_fun(double *state, double dt, double *out_7638325387504855371) {
   out_7638325387504855371[0] = 1;
   out_7638325387504855371[1] = 0;
   out_7638325387504855371[2] = 0;
   out_7638325387504855371[3] = 0;
   out_7638325387504855371[4] = 0;
   out_7638325387504855371[5] = 0;
   out_7638325387504855371[6] = 0;
   out_7638325387504855371[7] = 0;
   out_7638325387504855371[8] = 0;
   out_7638325387504855371[9] = 0;
   out_7638325387504855371[10] = 1;
   out_7638325387504855371[11] = 0;
   out_7638325387504855371[12] = 0;
   out_7638325387504855371[13] = 0;
   out_7638325387504855371[14] = 0;
   out_7638325387504855371[15] = 0;
   out_7638325387504855371[16] = 0;
   out_7638325387504855371[17] = 0;
   out_7638325387504855371[18] = 0;
   out_7638325387504855371[19] = 0;
   out_7638325387504855371[20] = 1;
   out_7638325387504855371[21] = 0;
   out_7638325387504855371[22] = 0;
   out_7638325387504855371[23] = 0;
   out_7638325387504855371[24] = 0;
   out_7638325387504855371[25] = 0;
   out_7638325387504855371[26] = 0;
   out_7638325387504855371[27] = 0;
   out_7638325387504855371[28] = 0;
   out_7638325387504855371[29] = 0;
   out_7638325387504855371[30] = 1;
   out_7638325387504855371[31] = 0;
   out_7638325387504855371[32] = 0;
   out_7638325387504855371[33] = 0;
   out_7638325387504855371[34] = 0;
   out_7638325387504855371[35] = 0;
   out_7638325387504855371[36] = 0;
   out_7638325387504855371[37] = 0;
   out_7638325387504855371[38] = 0;
   out_7638325387504855371[39] = 0;
   out_7638325387504855371[40] = 1;
   out_7638325387504855371[41] = 0;
   out_7638325387504855371[42] = 0;
   out_7638325387504855371[43] = 0;
   out_7638325387504855371[44] = 0;
   out_7638325387504855371[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_7638325387504855371[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_7638325387504855371[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_7638325387504855371[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_7638325387504855371[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_7638325387504855371[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_7638325387504855371[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_7638325387504855371[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_7638325387504855371[53] = -9.8100000000000005*dt;
   out_7638325387504855371[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_7638325387504855371[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_7638325387504855371[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_7638325387504855371[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_7638325387504855371[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_7638325387504855371[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_7638325387504855371[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_7638325387504855371[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_7638325387504855371[62] = 0;
   out_7638325387504855371[63] = 0;
   out_7638325387504855371[64] = 0;
   out_7638325387504855371[65] = 0;
   out_7638325387504855371[66] = 0;
   out_7638325387504855371[67] = 0;
   out_7638325387504855371[68] = 0;
   out_7638325387504855371[69] = 0;
   out_7638325387504855371[70] = 1;
   out_7638325387504855371[71] = 0;
   out_7638325387504855371[72] = 0;
   out_7638325387504855371[73] = 0;
   out_7638325387504855371[74] = 0;
   out_7638325387504855371[75] = 0;
   out_7638325387504855371[76] = 0;
   out_7638325387504855371[77] = 0;
   out_7638325387504855371[78] = 0;
   out_7638325387504855371[79] = 0;
   out_7638325387504855371[80] = 1;
}
void h_25(double *state, double *unused, double *out_3935694815555946964) {
   out_3935694815555946964[0] = state[6];
}
void H_25(double *state, double *unused, double *out_3633784864048405923) {
   out_3633784864048405923[0] = 0;
   out_3633784864048405923[1] = 0;
   out_3633784864048405923[2] = 0;
   out_3633784864048405923[3] = 0;
   out_3633784864048405923[4] = 0;
   out_3633784864048405923[5] = 0;
   out_3633784864048405923[6] = 1;
   out_3633784864048405923[7] = 0;
   out_3633784864048405923[8] = 0;
}
void h_24(double *state, double *unused, double *out_6796366672089269877) {
   out_6796366672089269877[0] = state[4];
   out_6796366672089269877[1] = state[5];
}
void H_24(double *state, double *unused, double *out_5806434463053905489) {
   out_5806434463053905489[0] = 0;
   out_5806434463053905489[1] = 0;
   out_5806434463053905489[2] = 0;
   out_5806434463053905489[3] = 0;
   out_5806434463053905489[4] = 1;
   out_5806434463053905489[5] = 0;
   out_5806434463053905489[6] = 0;
   out_5806434463053905489[7] = 0;
   out_5806434463053905489[8] = 0;
   out_5806434463053905489[9] = 0;
   out_5806434463053905489[10] = 0;
   out_5806434463053905489[11] = 0;
   out_5806434463053905489[12] = 0;
   out_5806434463053905489[13] = 0;
   out_5806434463053905489[14] = 1;
   out_5806434463053905489[15] = 0;
   out_5806434463053905489[16] = 0;
   out_5806434463053905489[17] = 0;
}
void h_30(double *state, double *unused, double *out_3790174991335291993) {
   out_3790174991335291993[0] = state[4];
}
void H_30(double *state, double *unused, double *out_1115451905541157296) {
   out_1115451905541157296[0] = 0;
   out_1115451905541157296[1] = 0;
   out_1115451905541157296[2] = 0;
   out_1115451905541157296[3] = 0;
   out_1115451905541157296[4] = 1;
   out_1115451905541157296[5] = 0;
   out_1115451905541157296[6] = 0;
   out_1115451905541157296[7] = 0;
   out_1115451905541157296[8] = 0;
}
void h_26(double *state, double *unused, double *out_8791848258996137638) {
   out_8791848258996137638[0] = state[7];
}
void H_26(double *state, double *unused, double *out_329258894287605322) {
   out_329258894287605322[0] = 0;
   out_329258894287605322[1] = 0;
   out_329258894287605322[2] = 0;
   out_329258894287605322[3] = 0;
   out_329258894287605322[4] = 0;
   out_329258894287605322[5] = 0;
   out_329258894287605322[6] = 0;
   out_329258894287605322[7] = 1;
   out_329258894287605322[8] = 0;
}
void h_27(double *state, double *unused, double *out_4879204668457609399) {
   out_4879204668457609399[0] = state[3];
}
void H_27(double *state, double *unused, double *out_3290215217341582207) {
   out_3290215217341582207[0] = 0;
   out_3290215217341582207[1] = 0;
   out_3290215217341582207[2] = 0;
   out_3290215217341582207[3] = 1;
   out_3290215217341582207[4] = 0;
   out_3290215217341582207[5] = 0;
   out_3290215217341582207[6] = 0;
   out_3290215217341582207[7] = 0;
   out_3290215217341582207[8] = 0;
}
void h_29(double *state, double *unused, double *out_4927661928533400333) {
   out_4927661928533400333[0] = state[1];
}
void H_29(double *state, double *unused, double *out_605220561226765112) {
   out_605220561226765112[0] = 0;
   out_605220561226765112[1] = 1;
   out_605220561226765112[2] = 0;
   out_605220561226765112[3] = 0;
   out_605220561226765112[4] = 0;
   out_605220561226765112[5] = 0;
   out_605220561226765112[6] = 0;
   out_605220561226765112[7] = 0;
   out_605220561226765112[8] = 0;
}
void h_28(double *state, double *unused, double *out_122468599203236246) {
   out_122468599203236246[0] = state[0];
}
void H_28(double *state, double *unused, double *out_5687619578296295686) {
   out_5687619578296295686[0] = 1;
   out_5687619578296295686[1] = 0;
   out_5687619578296295686[2] = 0;
   out_5687619578296295686[3] = 0;
   out_5687619578296295686[4] = 0;
   out_5687619578296295686[5] = 0;
   out_5687619578296295686[6] = 0;
   out_5687619578296295686[7] = 0;
   out_5687619578296295686[8] = 0;
}
void h_31(double *state, double *unused, double *out_2117612679297782603) {
   out_2117612679297782603[0] = state[8];
}
void H_31(double *state, double *unused, double *out_955466996520956798) {
   out_955466996520956798[0] = 0;
   out_955466996520956798[1] = 0;
   out_955466996520956798[2] = 0;
   out_955466996520956798[3] = 0;
   out_955466996520956798[4] = 0;
   out_955466996520956798[5] = 0;
   out_955466996520956798[6] = 0;
   out_955466996520956798[7] = 0;
   out_955466996520956798[8] = 1;
}
#include <eigen3/Eigen/Dense>
#include <iostream>

typedef Eigen::Matrix<double, DIM, DIM, Eigen::RowMajor> DDM;
typedef Eigen::Matrix<double, EDIM, EDIM, Eigen::RowMajor> EEM;
typedef Eigen::Matrix<double, DIM, EDIM, Eigen::RowMajor> DEM;

void predict(double *in_x, double *in_P, double *in_Q, double dt) {
  typedef Eigen::Matrix<double, MEDIM, MEDIM, Eigen::RowMajor> RRM;

  double nx[DIM] = {0};
  double in_F[EDIM*EDIM] = {0};

  // functions from sympy
  f_fun(in_x, dt, nx);
  F_fun(in_x, dt, in_F);


  EEM F(in_F);
  EEM P(in_P);
  EEM Q(in_Q);

  RRM F_main = F.topLeftCorner(MEDIM, MEDIM);
  P.topLeftCorner(MEDIM, MEDIM) = (F_main * P.topLeftCorner(MEDIM, MEDIM)) * F_main.transpose();
  P.topRightCorner(MEDIM, EDIM - MEDIM) = F_main * P.topRightCorner(MEDIM, EDIM - MEDIM);
  P.bottomLeftCorner(EDIM - MEDIM, MEDIM) = P.bottomLeftCorner(EDIM - MEDIM, MEDIM) * F_main.transpose();

  P = P + dt*Q;

  // copy out state
  memcpy(in_x, nx, DIM * sizeof(double));
  memcpy(in_P, P.data(), EDIM * EDIM * sizeof(double));
}

// note: extra_args dim only correct when null space projecting
// otherwise 1
template <int ZDIM, int EADIM, bool MAHA_TEST>
void update(double *in_x, double *in_P, Hfun h_fun, Hfun H_fun, Hfun Hea_fun, double *in_z, double *in_R, double *in_ea, double MAHA_THRESHOLD) {
  typedef Eigen::Matrix<double, ZDIM, ZDIM, Eigen::RowMajor> ZZM;
  typedef Eigen::Matrix<double, ZDIM, DIM, Eigen::RowMajor> ZDM;
  typedef Eigen::Matrix<double, Eigen::Dynamic, EDIM, Eigen::RowMajor> XEM;
  //typedef Eigen::Matrix<double, EDIM, ZDIM, Eigen::RowMajor> EZM;
  typedef Eigen::Matrix<double, Eigen::Dynamic, 1> X1M;
  typedef Eigen::Matrix<double, Eigen::Dynamic, Eigen::Dynamic, Eigen::RowMajor> XXM;

  double in_hx[ZDIM] = {0};
  double in_H[ZDIM * DIM] = {0};
  double in_H_mod[EDIM * DIM] = {0};
  double delta_x[EDIM] = {0};
  double x_new[DIM] = {0};


  // state x, P
  Eigen::Matrix<double, ZDIM, 1> z(in_z);
  EEM P(in_P);
  ZZM pre_R(in_R);

  // functions from sympy
  h_fun(in_x, in_ea, in_hx);
  H_fun(in_x, in_ea, in_H);
  ZDM pre_H(in_H);

  // get y (y = z - hx)
  Eigen::Matrix<double, ZDIM, 1> pre_y(in_hx); pre_y = z - pre_y;
  X1M y; XXM H; XXM R;
  if (Hea_fun){
    typedef Eigen::Matrix<double, ZDIM, EADIM, Eigen::RowMajor> ZAM;
    double in_Hea[ZDIM * EADIM] = {0};
    Hea_fun(in_x, in_ea, in_Hea);
    ZAM Hea(in_Hea);
    XXM A = Hea.transpose().fullPivLu().kernel();


    y = A.transpose() * pre_y;
    H = A.transpose() * pre_H;
    R = A.transpose() * pre_R * A;
  } else {
    y = pre_y;
    H = pre_H;
    R = pre_R;
  }
  // get modified H
  H_mod_fun(in_x, in_H_mod);
  DEM H_mod(in_H_mod);
  XEM H_err = H * H_mod;

  // Do mahalobis distance test
  if (MAHA_TEST){
    XXM a = (H_err * P * H_err.transpose() + R).inverse();
    double maha_dist = y.transpose() * a * y;
    if (maha_dist > MAHA_THRESHOLD){
      R = 1.0e16 * R;
    }
  }

  // Outlier resilient weighting
  double weight = 1;//(1.5)/(1 + y.squaredNorm()/R.sum());

  // kalman gains and I_KH
  XXM S = ((H_err * P) * H_err.transpose()) + R/weight;
  XEM KT = S.fullPivLu().solve(H_err * P.transpose());
  //EZM K = KT.transpose(); TODO: WHY DOES THIS NOT COMPILE?
  //EZM K = S.fullPivLu().solve(H_err * P.transpose()).transpose();
  //std::cout << "Here is the matrix rot:\n" << K << std::endl;
  EEM I_KH = Eigen::Matrix<double, EDIM, EDIM>::Identity() - (KT.transpose() * H_err);

  // update state by injecting dx
  Eigen::Matrix<double, EDIM, 1> dx(delta_x);
  dx  = (KT.transpose() * y);
  memcpy(delta_x, dx.data(), EDIM * sizeof(double));
  err_fun(in_x, delta_x, x_new);
  Eigen::Matrix<double, DIM, 1> x(x_new);

  // update cov
  P = ((I_KH * P) * I_KH.transpose()) + ((KT.transpose() * R) * KT);

  // copy out state
  memcpy(in_x, x.data(), DIM * sizeof(double));
  memcpy(in_P, P.data(), EDIM * EDIM * sizeof(double));
  memcpy(in_z, y.data(), y.rows() * sizeof(double));
}




}
extern "C" {

void car_update_25(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_25, H_25, NULL, in_z, in_R, in_ea, MAHA_THRESH_25);
}
void car_update_24(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<2, 3, 0>(in_x, in_P, h_24, H_24, NULL, in_z, in_R, in_ea, MAHA_THRESH_24);
}
void car_update_30(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_30, H_30, NULL, in_z, in_R, in_ea, MAHA_THRESH_30);
}
void car_update_26(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_26, H_26, NULL, in_z, in_R, in_ea, MAHA_THRESH_26);
}
void car_update_27(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_27, H_27, NULL, in_z, in_R, in_ea, MAHA_THRESH_27);
}
void car_update_29(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_29, H_29, NULL, in_z, in_R, in_ea, MAHA_THRESH_29);
}
void car_update_28(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_28, H_28, NULL, in_z, in_R, in_ea, MAHA_THRESH_28);
}
void car_update_31(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_31, H_31, NULL, in_z, in_R, in_ea, MAHA_THRESH_31);
}
void car_err_fun(double *nom_x, double *delta_x, double *out_4251681860653429657) {
  err_fun(nom_x, delta_x, out_4251681860653429657);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_4799215824192635798) {
  inv_err_fun(nom_x, true_x, out_4799215824192635798);
}
void car_H_mod_fun(double *state, double *out_318226151794054394) {
  H_mod_fun(state, out_318226151794054394);
}
void car_f_fun(double *state, double dt, double *out_6040091135663911588) {
  f_fun(state,  dt, out_6040091135663911588);
}
void car_F_fun(double *state, double dt, double *out_7638325387504855371) {
  F_fun(state,  dt, out_7638325387504855371);
}
void car_h_25(double *state, double *unused, double *out_3935694815555946964) {
  h_25(state, unused, out_3935694815555946964);
}
void car_H_25(double *state, double *unused, double *out_3633784864048405923) {
  H_25(state, unused, out_3633784864048405923);
}
void car_h_24(double *state, double *unused, double *out_6796366672089269877) {
  h_24(state, unused, out_6796366672089269877);
}
void car_H_24(double *state, double *unused, double *out_5806434463053905489) {
  H_24(state, unused, out_5806434463053905489);
}
void car_h_30(double *state, double *unused, double *out_3790174991335291993) {
  h_30(state, unused, out_3790174991335291993);
}
void car_H_30(double *state, double *unused, double *out_1115451905541157296) {
  H_30(state, unused, out_1115451905541157296);
}
void car_h_26(double *state, double *unused, double *out_8791848258996137638) {
  h_26(state, unused, out_8791848258996137638);
}
void car_H_26(double *state, double *unused, double *out_329258894287605322) {
  H_26(state, unused, out_329258894287605322);
}
void car_h_27(double *state, double *unused, double *out_4879204668457609399) {
  h_27(state, unused, out_4879204668457609399);
}
void car_H_27(double *state, double *unused, double *out_3290215217341582207) {
  H_27(state, unused, out_3290215217341582207);
}
void car_h_29(double *state, double *unused, double *out_4927661928533400333) {
  h_29(state, unused, out_4927661928533400333);
}
void car_H_29(double *state, double *unused, double *out_605220561226765112) {
  H_29(state, unused, out_605220561226765112);
}
void car_h_28(double *state, double *unused, double *out_122468599203236246) {
  h_28(state, unused, out_122468599203236246);
}
void car_H_28(double *state, double *unused, double *out_5687619578296295686) {
  H_28(state, unused, out_5687619578296295686);
}
void car_h_31(double *state, double *unused, double *out_2117612679297782603) {
  h_31(state, unused, out_2117612679297782603);
}
void car_H_31(double *state, double *unused, double *out_955466996520956798) {
  H_31(state, unused, out_955466996520956798);
}
void car_predict(double *in_x, double *in_P, double *in_Q, double dt) {
  predict(in_x, in_P, in_Q, dt);
}
void car_set_mass(double x) {
  set_mass(x);
}
void car_set_rotational_inertia(double x) {
  set_rotational_inertia(x);
}
void car_set_center_to_front(double x) {
  set_center_to_front(x);
}
void car_set_center_to_rear(double x) {
  set_center_to_rear(x);
}
void car_set_stiffness_front(double x) {
  set_stiffness_front(x);
}
void car_set_stiffness_rear(double x) {
  set_stiffness_rear(x);
}
}

const EKF car = {
  .name = "car",
  .kinds = { 25, 24, 30, 26, 27, 29, 28, 31 },
  .feature_kinds = {  },
  .f_fun = car_f_fun,
  .F_fun = car_F_fun,
  .err_fun = car_err_fun,
  .inv_err_fun = car_inv_err_fun,
  .H_mod_fun = car_H_mod_fun,
  .predict = car_predict,
  .hs = {
    { 25, car_h_25 },
    { 24, car_h_24 },
    { 30, car_h_30 },
    { 26, car_h_26 },
    { 27, car_h_27 },
    { 29, car_h_29 },
    { 28, car_h_28 },
    { 31, car_h_31 },
  },
  .Hs = {
    { 25, car_H_25 },
    { 24, car_H_24 },
    { 30, car_H_30 },
    { 26, car_H_26 },
    { 27, car_H_27 },
    { 29, car_H_29 },
    { 28, car_H_28 },
    { 31, car_H_31 },
  },
  .updates = {
    { 25, car_update_25 },
    { 24, car_update_24 },
    { 30, car_update_30 },
    { 26, car_update_26 },
    { 27, car_update_27 },
    { 29, car_update_29 },
    { 28, car_update_28 },
    { 31, car_update_31 },
  },
  .Hes = {
  },
  .sets = {
    { "mass", car_set_mass },
    { "rotational_inertia", car_set_rotational_inertia },
    { "center_to_front", car_set_center_to_front },
    { "center_to_rear", car_set_center_to_rear },
    { "stiffness_front", car_set_stiffness_front },
    { "stiffness_rear", car_set_stiffness_rear },
  },
  .extra_routines = {
  },
};

ekf_lib_init(car)
