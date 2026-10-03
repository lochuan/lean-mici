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
void live_H(double *in_vec, double *out_1635220639804289325);
void live_err_fun(double *nom_x, double *delta_x, double *out_3531595904595962812);
void live_inv_err_fun(double *nom_x, double *true_x, double *out_527084627129474912);
void live_H_mod_fun(double *state, double *out_7531963635542141546);
void live_f_fun(double *state, double dt, double *out_6392030034155528920);
void live_F_fun(double *state, double dt, double *out_1721049028225590694);
void live_h_4(double *state, double *unused, double *out_2456396512567268757);
void live_H_4(double *state, double *unused, double *out_6356919430993674375);
void live_h_9(double *state, double *unused, double *out_8175158772449130468);
void live_H_9(double *state, double *unused, double *out_4802605707451429771);
void live_h_10(double *state, double *unused, double *out_3749592149898020644);
void live_H_10(double *state, double *unused, double *out_4597340020877637393);
void live_h_12(double *state, double *unused, double *out_1420438558175774936);
void live_H_12(double *state, double *unused, double *out_24338946049058621);
void live_h_35(double *state, double *unused, double *out_5809945092820300130);
void live_H_35(double *state, double *unused, double *out_4324805202358901737);
void live_h_32(double *state, double *unused, double *out_4124812046221184186);
void live_H_32(double *state, double *unused, double *out_1619780689457151800);
void live_h_13(double *state, double *unused, double *out_2166693940738262291);
void live_H_13(double *state, double *unused, double *out_8395259339074919592);
void live_h_14(double *state, double *unused, double *out_8175158772449130468);
void live_H_14(double *state, double *unused, double *out_4802605707451429771);
void live_h_33(double *state, double *unused, double *out_2330005963337963395);
void live_H_33(double *state, double *unused, double *out_1174248197720044133);
void live_predict(double *in_x, double *in_P, double *in_Q, double dt);
}