#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void car_update_25(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_24(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_30(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_26(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_27(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_29(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_28(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_31(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_err_fun(double *nom_x, double *delta_x, double *out_1588994344372544803);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_6630812614697802148);
void car_H_mod_fun(double *state, double *out_6909972393368041384);
void car_f_fun(double *state, double dt, double *out_4235527098183537761);
void car_F_fun(double *state, double dt, double *out_3246527070410094349);
void car_h_25(double *state, double *unused, double *out_8443291009080466273);
void car_H_25(double *state, double *unused, double *out_3615433647367650427);
void car_h_24(double *state, double *unused, double *out_1527990759341730746);
void car_H_24(double *state, double *unused, double *out_57508048565327291);
void car_h_30(double *state, double *unused, double *out_3128682960391481066);
void car_H_30(double *state, double *unused, double *out_8143129977495258625);
void car_h_26(double *state, double *unused, double *out_1960939159315339329);
void car_H_26(double *state, double *unused, double *out_7356936966241706651);
void car_h_27(double *state, double *unused, double *out_1951704431223188910);
void car_H_27(double *state, double *unused, double *out_5919535906311315408);
void car_h_29(double *state, double *unused, double *out_7186012853460662272);
void car_H_29(double *state, double *unused, double *out_7632898633180866441);
void car_h_28(double *state, double *unused, double *out_6953377698884034555);
void car_H_28(double *state, double *unused, double *out_5731446423459154601);
void car_h_31(double *state, double *unused, double *out_7041326550930009305);
void car_H_31(double *state, double *unused, double *out_7983145068475058127);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}