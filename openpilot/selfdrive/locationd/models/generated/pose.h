#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_5703369478197646593);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_8101996954815542477);
void pose_H_mod_fun(double *state, double *out_767115652523811832);
void pose_f_fun(double *state, double dt, double *out_5020659945953845111);
void pose_F_fun(double *state, double dt, double *out_4864829611890037349);
void pose_h_4(double *state, double *unused, double *out_9073493681667836505);
void pose_H_4(double *state, double *unused, double *out_8245868767389872660);
void pose_h_10(double *state, double *unused, double *out_7316638718416108353);
void pose_H_10(double *state, double *unused, double *out_1400604490691219462);
void pose_h_13(double *state, double *unused, double *out_3331559375824327691);
void pose_H_13(double *state, double *unused, double *out_6988601480987346155);
void pose_h_14(double *state, double *unused, double *out_5696773055453158029);
void pose_H_14(double *state, double *unused, double *out_6237634449980194427);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}