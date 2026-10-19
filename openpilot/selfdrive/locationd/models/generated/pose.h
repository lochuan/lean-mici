#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_376508033301991984);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_3293932473507733269);
void pose_H_mod_fun(double *state, double *out_8189327656671088405);
void pose_f_fun(double *state, double dt, double *out_7345618461576849553);
void pose_F_fun(double *state, double dt, double *out_1545513811425739451);
void pose_h_4(double *state, double *unused, double *out_4720738351192482144);
void pose_H_4(double *state, double *unused, double *out_1260804976980367599);
void pose_h_10(double *state, double *unused, double *out_3527418583963507715);
void pose_H_10(double *state, double *unused, double *out_7685492195082252267);
void pose_h_13(double *state, double *unused, double *out_7066220906659889628);
void pose_H_13(double *state, double *unused, double *out_1951468848351965202);
void pose_h_14(double *state, double *unused, double *out_8413492641494458851);
void pose_H_14(double *state, double *unused, double *out_2702435879359116930);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}