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
void err_fun(double *nom_x, double *delta_x, double *out_1588994344372544803) {
   out_1588994344372544803[0] = delta_x[0] + nom_x[0];
   out_1588994344372544803[1] = delta_x[1] + nom_x[1];
   out_1588994344372544803[2] = delta_x[2] + nom_x[2];
   out_1588994344372544803[3] = delta_x[3] + nom_x[3];
   out_1588994344372544803[4] = delta_x[4] + nom_x[4];
   out_1588994344372544803[5] = delta_x[5] + nom_x[5];
   out_1588994344372544803[6] = delta_x[6] + nom_x[6];
   out_1588994344372544803[7] = delta_x[7] + nom_x[7];
   out_1588994344372544803[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_6630812614697802148) {
   out_6630812614697802148[0] = -nom_x[0] + true_x[0];
   out_6630812614697802148[1] = -nom_x[1] + true_x[1];
   out_6630812614697802148[2] = -nom_x[2] + true_x[2];
   out_6630812614697802148[3] = -nom_x[3] + true_x[3];
   out_6630812614697802148[4] = -nom_x[4] + true_x[4];
   out_6630812614697802148[5] = -nom_x[5] + true_x[5];
   out_6630812614697802148[6] = -nom_x[6] + true_x[6];
   out_6630812614697802148[7] = -nom_x[7] + true_x[7];
   out_6630812614697802148[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_6909972393368041384) {
   out_6909972393368041384[0] = 1.0;
   out_6909972393368041384[1] = 0.0;
   out_6909972393368041384[2] = 0.0;
   out_6909972393368041384[3] = 0.0;
   out_6909972393368041384[4] = 0.0;
   out_6909972393368041384[5] = 0.0;
   out_6909972393368041384[6] = 0.0;
   out_6909972393368041384[7] = 0.0;
   out_6909972393368041384[8] = 0.0;
   out_6909972393368041384[9] = 0.0;
   out_6909972393368041384[10] = 1.0;
   out_6909972393368041384[11] = 0.0;
   out_6909972393368041384[12] = 0.0;
   out_6909972393368041384[13] = 0.0;
   out_6909972393368041384[14] = 0.0;
   out_6909972393368041384[15] = 0.0;
   out_6909972393368041384[16] = 0.0;
   out_6909972393368041384[17] = 0.0;
   out_6909972393368041384[18] = 0.0;
   out_6909972393368041384[19] = 0.0;
   out_6909972393368041384[20] = 1.0;
   out_6909972393368041384[21] = 0.0;
   out_6909972393368041384[22] = 0.0;
   out_6909972393368041384[23] = 0.0;
   out_6909972393368041384[24] = 0.0;
   out_6909972393368041384[25] = 0.0;
   out_6909972393368041384[26] = 0.0;
   out_6909972393368041384[27] = 0.0;
   out_6909972393368041384[28] = 0.0;
   out_6909972393368041384[29] = 0.0;
   out_6909972393368041384[30] = 1.0;
   out_6909972393368041384[31] = 0.0;
   out_6909972393368041384[32] = 0.0;
   out_6909972393368041384[33] = 0.0;
   out_6909972393368041384[34] = 0.0;
   out_6909972393368041384[35] = 0.0;
   out_6909972393368041384[36] = 0.0;
   out_6909972393368041384[37] = 0.0;
   out_6909972393368041384[38] = 0.0;
   out_6909972393368041384[39] = 0.0;
   out_6909972393368041384[40] = 1.0;
   out_6909972393368041384[41] = 0.0;
   out_6909972393368041384[42] = 0.0;
   out_6909972393368041384[43] = 0.0;
   out_6909972393368041384[44] = 0.0;
   out_6909972393368041384[45] = 0.0;
   out_6909972393368041384[46] = 0.0;
   out_6909972393368041384[47] = 0.0;
   out_6909972393368041384[48] = 0.0;
   out_6909972393368041384[49] = 0.0;
   out_6909972393368041384[50] = 1.0;
   out_6909972393368041384[51] = 0.0;
   out_6909972393368041384[52] = 0.0;
   out_6909972393368041384[53] = 0.0;
   out_6909972393368041384[54] = 0.0;
   out_6909972393368041384[55] = 0.0;
   out_6909972393368041384[56] = 0.0;
   out_6909972393368041384[57] = 0.0;
   out_6909972393368041384[58] = 0.0;
   out_6909972393368041384[59] = 0.0;
   out_6909972393368041384[60] = 1.0;
   out_6909972393368041384[61] = 0.0;
   out_6909972393368041384[62] = 0.0;
   out_6909972393368041384[63] = 0.0;
   out_6909972393368041384[64] = 0.0;
   out_6909972393368041384[65] = 0.0;
   out_6909972393368041384[66] = 0.0;
   out_6909972393368041384[67] = 0.0;
   out_6909972393368041384[68] = 0.0;
   out_6909972393368041384[69] = 0.0;
   out_6909972393368041384[70] = 1.0;
   out_6909972393368041384[71] = 0.0;
   out_6909972393368041384[72] = 0.0;
   out_6909972393368041384[73] = 0.0;
   out_6909972393368041384[74] = 0.0;
   out_6909972393368041384[75] = 0.0;
   out_6909972393368041384[76] = 0.0;
   out_6909972393368041384[77] = 0.0;
   out_6909972393368041384[78] = 0.0;
   out_6909972393368041384[79] = 0.0;
   out_6909972393368041384[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_4235527098183537761) {
   out_4235527098183537761[0] = state[0];
   out_4235527098183537761[1] = state[1];
   out_4235527098183537761[2] = state[2];
   out_4235527098183537761[3] = state[3];
   out_4235527098183537761[4] = state[4];
   out_4235527098183537761[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_4235527098183537761[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_4235527098183537761[7] = state[7];
   out_4235527098183537761[8] = state[8];
}
void F_fun(double *state, double dt, double *out_3246527070410094349) {
   out_3246527070410094349[0] = 1;
   out_3246527070410094349[1] = 0;
   out_3246527070410094349[2] = 0;
   out_3246527070410094349[3] = 0;
   out_3246527070410094349[4] = 0;
   out_3246527070410094349[5] = 0;
   out_3246527070410094349[6] = 0;
   out_3246527070410094349[7] = 0;
   out_3246527070410094349[8] = 0;
   out_3246527070410094349[9] = 0;
   out_3246527070410094349[10] = 1;
   out_3246527070410094349[11] = 0;
   out_3246527070410094349[12] = 0;
   out_3246527070410094349[13] = 0;
   out_3246527070410094349[14] = 0;
   out_3246527070410094349[15] = 0;
   out_3246527070410094349[16] = 0;
   out_3246527070410094349[17] = 0;
   out_3246527070410094349[18] = 0;
   out_3246527070410094349[19] = 0;
   out_3246527070410094349[20] = 1;
   out_3246527070410094349[21] = 0;
   out_3246527070410094349[22] = 0;
   out_3246527070410094349[23] = 0;
   out_3246527070410094349[24] = 0;
   out_3246527070410094349[25] = 0;
   out_3246527070410094349[26] = 0;
   out_3246527070410094349[27] = 0;
   out_3246527070410094349[28] = 0;
   out_3246527070410094349[29] = 0;
   out_3246527070410094349[30] = 1;
   out_3246527070410094349[31] = 0;
   out_3246527070410094349[32] = 0;
   out_3246527070410094349[33] = 0;
   out_3246527070410094349[34] = 0;
   out_3246527070410094349[35] = 0;
   out_3246527070410094349[36] = 0;
   out_3246527070410094349[37] = 0;
   out_3246527070410094349[38] = 0;
   out_3246527070410094349[39] = 0;
   out_3246527070410094349[40] = 1;
   out_3246527070410094349[41] = 0;
   out_3246527070410094349[42] = 0;
   out_3246527070410094349[43] = 0;
   out_3246527070410094349[44] = 0;
   out_3246527070410094349[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_3246527070410094349[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_3246527070410094349[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_3246527070410094349[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_3246527070410094349[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_3246527070410094349[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_3246527070410094349[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_3246527070410094349[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_3246527070410094349[53] = -9.8100000000000005*dt;
   out_3246527070410094349[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_3246527070410094349[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_3246527070410094349[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_3246527070410094349[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_3246527070410094349[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_3246527070410094349[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_3246527070410094349[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_3246527070410094349[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_3246527070410094349[62] = 0;
   out_3246527070410094349[63] = 0;
   out_3246527070410094349[64] = 0;
   out_3246527070410094349[65] = 0;
   out_3246527070410094349[66] = 0;
   out_3246527070410094349[67] = 0;
   out_3246527070410094349[68] = 0;
   out_3246527070410094349[69] = 0;
   out_3246527070410094349[70] = 1;
   out_3246527070410094349[71] = 0;
   out_3246527070410094349[72] = 0;
   out_3246527070410094349[73] = 0;
   out_3246527070410094349[74] = 0;
   out_3246527070410094349[75] = 0;
   out_3246527070410094349[76] = 0;
   out_3246527070410094349[77] = 0;
   out_3246527070410094349[78] = 0;
   out_3246527070410094349[79] = 0;
   out_3246527070410094349[80] = 1;
}
void h_25(double *state, double *unused, double *out_8443291009080466273) {
   out_8443291009080466273[0] = state[6];
}
void H_25(double *state, double *unused, double *out_3615433647367650427) {
   out_3615433647367650427[0] = 0;
   out_3615433647367650427[1] = 0;
   out_3615433647367650427[2] = 0;
   out_3615433647367650427[3] = 0;
   out_3615433647367650427[4] = 0;
   out_3615433647367650427[5] = 0;
   out_3615433647367650427[6] = 1;
   out_3615433647367650427[7] = 0;
   out_3615433647367650427[8] = 0;
}
void h_24(double *state, double *unused, double *out_1527990759341730746) {
   out_1527990759341730746[0] = state[4];
   out_1527990759341730746[1] = state[5];
}
void H_24(double *state, double *unused, double *out_57508048565327291) {
   out_57508048565327291[0] = 0;
   out_57508048565327291[1] = 0;
   out_57508048565327291[2] = 0;
   out_57508048565327291[3] = 0;
   out_57508048565327291[4] = 1;
   out_57508048565327291[5] = 0;
   out_57508048565327291[6] = 0;
   out_57508048565327291[7] = 0;
   out_57508048565327291[8] = 0;
   out_57508048565327291[9] = 0;
   out_57508048565327291[10] = 0;
   out_57508048565327291[11] = 0;
   out_57508048565327291[12] = 0;
   out_57508048565327291[13] = 0;
   out_57508048565327291[14] = 1;
   out_57508048565327291[15] = 0;
   out_57508048565327291[16] = 0;
   out_57508048565327291[17] = 0;
}
void h_30(double *state, double *unused, double *out_3128682960391481066) {
   out_3128682960391481066[0] = state[4];
}
void H_30(double *state, double *unused, double *out_8143129977495258625) {
   out_8143129977495258625[0] = 0;
   out_8143129977495258625[1] = 0;
   out_8143129977495258625[2] = 0;
   out_8143129977495258625[3] = 0;
   out_8143129977495258625[4] = 1;
   out_8143129977495258625[5] = 0;
   out_8143129977495258625[6] = 0;
   out_8143129977495258625[7] = 0;
   out_8143129977495258625[8] = 0;
}
void h_26(double *state, double *unused, double *out_1960939159315339329) {
   out_1960939159315339329[0] = state[7];
}
void H_26(double *state, double *unused, double *out_7356936966241706651) {
   out_7356936966241706651[0] = 0;
   out_7356936966241706651[1] = 0;
   out_7356936966241706651[2] = 0;
   out_7356936966241706651[3] = 0;
   out_7356936966241706651[4] = 0;
   out_7356936966241706651[5] = 0;
   out_7356936966241706651[6] = 0;
   out_7356936966241706651[7] = 1;
   out_7356936966241706651[8] = 0;
}
void h_27(double *state, double *unused, double *out_1951704431223188910) {
   out_1951704431223188910[0] = state[3];
}
void H_27(double *state, double *unused, double *out_5919535906311315408) {
   out_5919535906311315408[0] = 0;
   out_5919535906311315408[1] = 0;
   out_5919535906311315408[2] = 0;
   out_5919535906311315408[3] = 1;
   out_5919535906311315408[4] = 0;
   out_5919535906311315408[5] = 0;
   out_5919535906311315408[6] = 0;
   out_5919535906311315408[7] = 0;
   out_5919535906311315408[8] = 0;
}
void h_29(double *state, double *unused, double *out_7186012853460662272) {
   out_7186012853460662272[0] = state[1];
}
void H_29(double *state, double *unused, double *out_7632898633180866441) {
   out_7632898633180866441[0] = 0;
   out_7632898633180866441[1] = 1;
   out_7632898633180866441[2] = 0;
   out_7632898633180866441[3] = 0;
   out_7632898633180866441[4] = 0;
   out_7632898633180866441[5] = 0;
   out_7632898633180866441[6] = 0;
   out_7632898633180866441[7] = 0;
   out_7632898633180866441[8] = 0;
}
void h_28(double *state, double *unused, double *out_6953377698884034555) {
   out_6953377698884034555[0] = state[0];
}
void H_28(double *state, double *unused, double *out_5731446423459154601) {
   out_5731446423459154601[0] = 1;
   out_5731446423459154601[1] = 0;
   out_5731446423459154601[2] = 0;
   out_5731446423459154601[3] = 0;
   out_5731446423459154601[4] = 0;
   out_5731446423459154601[5] = 0;
   out_5731446423459154601[6] = 0;
   out_5731446423459154601[7] = 0;
   out_5731446423459154601[8] = 0;
}
void h_31(double *state, double *unused, double *out_7041326550930009305) {
   out_7041326550930009305[0] = state[8];
}
void H_31(double *state, double *unused, double *out_7983145068475058127) {
   out_7983145068475058127[0] = 0;
   out_7983145068475058127[1] = 0;
   out_7983145068475058127[2] = 0;
   out_7983145068475058127[3] = 0;
   out_7983145068475058127[4] = 0;
   out_7983145068475058127[5] = 0;
   out_7983145068475058127[6] = 0;
   out_7983145068475058127[7] = 0;
   out_7983145068475058127[8] = 1;
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
void car_err_fun(double *nom_x, double *delta_x, double *out_1588994344372544803) {
  err_fun(nom_x, delta_x, out_1588994344372544803);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_6630812614697802148) {
  inv_err_fun(nom_x, true_x, out_6630812614697802148);
}
void car_H_mod_fun(double *state, double *out_6909972393368041384) {
  H_mod_fun(state, out_6909972393368041384);
}
void car_f_fun(double *state, double dt, double *out_4235527098183537761) {
  f_fun(state,  dt, out_4235527098183537761);
}
void car_F_fun(double *state, double dt, double *out_3246527070410094349) {
  F_fun(state,  dt, out_3246527070410094349);
}
void car_h_25(double *state, double *unused, double *out_8443291009080466273) {
  h_25(state, unused, out_8443291009080466273);
}
void car_H_25(double *state, double *unused, double *out_3615433647367650427) {
  H_25(state, unused, out_3615433647367650427);
}
void car_h_24(double *state, double *unused, double *out_1527990759341730746) {
  h_24(state, unused, out_1527990759341730746);
}
void car_H_24(double *state, double *unused, double *out_57508048565327291) {
  H_24(state, unused, out_57508048565327291);
}
void car_h_30(double *state, double *unused, double *out_3128682960391481066) {
  h_30(state, unused, out_3128682960391481066);
}
void car_H_30(double *state, double *unused, double *out_8143129977495258625) {
  H_30(state, unused, out_8143129977495258625);
}
void car_h_26(double *state, double *unused, double *out_1960939159315339329) {
  h_26(state, unused, out_1960939159315339329);
}
void car_H_26(double *state, double *unused, double *out_7356936966241706651) {
  H_26(state, unused, out_7356936966241706651);
}
void car_h_27(double *state, double *unused, double *out_1951704431223188910) {
  h_27(state, unused, out_1951704431223188910);
}
void car_H_27(double *state, double *unused, double *out_5919535906311315408) {
  H_27(state, unused, out_5919535906311315408);
}
void car_h_29(double *state, double *unused, double *out_7186012853460662272) {
  h_29(state, unused, out_7186012853460662272);
}
void car_H_29(double *state, double *unused, double *out_7632898633180866441) {
  H_29(state, unused, out_7632898633180866441);
}
void car_h_28(double *state, double *unused, double *out_6953377698884034555) {
  h_28(state, unused, out_6953377698884034555);
}
void car_H_28(double *state, double *unused, double *out_5731446423459154601) {
  H_28(state, unused, out_5731446423459154601);
}
void car_h_31(double *state, double *unused, double *out_7041326550930009305) {
  h_31(state, unused, out_7041326550930009305);
}
void car_H_31(double *state, double *unused, double *out_7983145068475058127) {
  H_31(state, unused, out_7983145068475058127);
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
