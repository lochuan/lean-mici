#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void live_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_9(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_12(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_35(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_32(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_33(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_H(double *in_vec, double *out_5418146744959722956);
void live_err_fun(double *nom_x, double *delta_x, double *out_6289039315556162828);
void live_inv_err_fun(double *nom_x, double *true_x, double *out_1785055711766825220);
void live_H_mod_fun(double *state, double *out_3192057764645172525);
void live_f_fun(double *state, double dt, double *out_6632951458862898074);
void live_F_fun(double *state, double dt, double *out_8577119807617260826);
void live_h_4(double *state, double *unused, double *out_8358674515337021355);
void live_H_4(double *state, double *unused, double *out_4036864294398578980);
void live_h_9(double *state, double *unused, double *out_2453319135299901362);
void live_H_9(double *state, double *unused, double *out_3795674647768988335);
void live_h_10(double *state, double *unused, double *out_2639250794122815536);
void live_H_10(double *state, double *unused, double *out_2630084525484470747);
void live_h_12(double *state, double *unused, double *out_8431207723681403716);
void live_H_12(double *state, double *unused, double *out_982592113633382815);
void live_h_35(double *state, double *unused, double *out_7315092869289217060);
void live_H_35(double *state, double *unused, double *out_670202237025971604);
void live_h_32(double *state, double *unused, double *out_1384683837725337343);
void live_H_32(double *state, double *unused, double *out_1270636100307037197);
void live_h_13(double *state, double *unused, double *out_4402130949920405306);
void live_H_13(double *state, double *unused, double *out_8124679032802732196);
void live_h_14(double *state, double *unused, double *out_2453319135299901362);
void live_H_14(double *state, double *unused, double *out_3795674647768988335);
void live_h_33(double *state, double *unused, double *out_3822357649747494356);
void live_H_33(double *state, double *unused, double *out_2480354767612886000);
void live_predict(double *in_x, double *in_P, double *in_Q, double dt);
}