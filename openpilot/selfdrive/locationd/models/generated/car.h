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
void car_err_fun(double *nom_x, double *delta_x, double *out_4251681860653429657);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_4799215824192635798);
void car_H_mod_fun(double *state, double *out_318226151794054394);
void car_f_fun(double *state, double dt, double *out_6040091135663911588);
void car_F_fun(double *state, double dt, double *out_7638325387504855371);
void car_h_25(double *state, double *unused, double *out_3935694815555946964);
void car_H_25(double *state, double *unused, double *out_3633784864048405923);
void car_h_24(double *state, double *unused, double *out_6796366672089269877);
void car_H_24(double *state, double *unused, double *out_5806434463053905489);
void car_h_30(double *state, double *unused, double *out_3790174991335291993);
void car_H_30(double *state, double *unused, double *out_1115451905541157296);
void car_h_26(double *state, double *unused, double *out_8791848258996137638);
void car_H_26(double *state, double *unused, double *out_329258894287605322);
void car_h_27(double *state, double *unused, double *out_4879204668457609399);
void car_H_27(double *state, double *unused, double *out_3290215217341582207);
void car_h_29(double *state, double *unused, double *out_4927661928533400333);
void car_H_29(double *state, double *unused, double *out_605220561226765112);
void car_h_28(double *state, double *unused, double *out_122468599203236246);
void car_H_28(double *state, double *unused, double *out_5687619578296295686);
void car_h_31(double *state, double *unused, double *out_2117612679297782603);
void car_H_31(double *state, double *unused, double *out_955466996520956798);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}