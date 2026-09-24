#include "pose.h"

namespace {
#define DIM 18
#define EDIM 18
#define MEDIM 18
typedef void (*Hfun)(double *, double *, double *);
const static double MAHA_THRESH_4 = 7.814727903251177;
const static double MAHA_THRESH_10 = 7.814727903251177;
const static double MAHA_THRESH_13 = 7.814727903251177;
const static double MAHA_THRESH_14 = 7.814727903251177;

/******************************************************************************
 *                      Code generated with SymPy 1.14.0                      *
 *                                                                            *
 *              See http://www.sympy.org/ for more information.               *
 *                                                                            *
 *                         This file is part of 'ekf'                         *
 ******************************************************************************/
void err_fun(double *nom_x, double *delta_x, double *out_5703369478197646593) {
   out_5703369478197646593[0] = delta_x[0] + nom_x[0];
   out_5703369478197646593[1] = delta_x[1] + nom_x[1];
   out_5703369478197646593[2] = delta_x[2] + nom_x[2];
   out_5703369478197646593[3] = delta_x[3] + nom_x[3];
   out_5703369478197646593[4] = delta_x[4] + nom_x[4];
   out_5703369478197646593[5] = delta_x[5] + nom_x[5];
   out_5703369478197646593[6] = delta_x[6] + nom_x[6];
   out_5703369478197646593[7] = delta_x[7] + nom_x[7];
   out_5703369478197646593[8] = delta_x[8] + nom_x[8];
   out_5703369478197646593[9] = delta_x[9] + nom_x[9];
   out_5703369478197646593[10] = delta_x[10] + nom_x[10];
   out_5703369478197646593[11] = delta_x[11] + nom_x[11];
   out_5703369478197646593[12] = delta_x[12] + nom_x[12];
   out_5703369478197646593[13] = delta_x[13] + nom_x[13];
   out_5703369478197646593[14] = delta_x[14] + nom_x[14];
   out_5703369478197646593[15] = delta_x[15] + nom_x[15];
   out_5703369478197646593[16] = delta_x[16] + nom_x[16];
   out_5703369478197646593[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_8101996954815542477) {
   out_8101996954815542477[0] = -nom_x[0] + true_x[0];
   out_8101996954815542477[1] = -nom_x[1] + true_x[1];
   out_8101996954815542477[2] = -nom_x[2] + true_x[2];
   out_8101996954815542477[3] = -nom_x[3] + true_x[3];
   out_8101996954815542477[4] = -nom_x[4] + true_x[4];
   out_8101996954815542477[5] = -nom_x[5] + true_x[5];
   out_8101996954815542477[6] = -nom_x[6] + true_x[6];
   out_8101996954815542477[7] = -nom_x[7] + true_x[7];
   out_8101996954815542477[8] = -nom_x[8] + true_x[8];
   out_8101996954815542477[9] = -nom_x[9] + true_x[9];
   out_8101996954815542477[10] = -nom_x[10] + true_x[10];
   out_8101996954815542477[11] = -nom_x[11] + true_x[11];
   out_8101996954815542477[12] = -nom_x[12] + true_x[12];
   out_8101996954815542477[13] = -nom_x[13] + true_x[13];
   out_8101996954815542477[14] = -nom_x[14] + true_x[14];
   out_8101996954815542477[15] = -nom_x[15] + true_x[15];
   out_8101996954815542477[16] = -nom_x[16] + true_x[16];
   out_8101996954815542477[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_767115652523811832) {
   out_767115652523811832[0] = 1.0;
   out_767115652523811832[1] = 0.0;
   out_767115652523811832[2] = 0.0;
   out_767115652523811832[3] = 0.0;
   out_767115652523811832[4] = 0.0;
   out_767115652523811832[5] = 0.0;
   out_767115652523811832[6] = 0.0;
   out_767115652523811832[7] = 0.0;
   out_767115652523811832[8] = 0.0;
   out_767115652523811832[9] = 0.0;
   out_767115652523811832[10] = 0.0;
   out_767115652523811832[11] = 0.0;
   out_767115652523811832[12] = 0.0;
   out_767115652523811832[13] = 0.0;
   out_767115652523811832[14] = 0.0;
   out_767115652523811832[15] = 0.0;
   out_767115652523811832[16] = 0.0;
   out_767115652523811832[17] = 0.0;
   out_767115652523811832[18] = 0.0;
   out_767115652523811832[19] = 1.0;
   out_767115652523811832[20] = 0.0;
   out_767115652523811832[21] = 0.0;
   out_767115652523811832[22] = 0.0;
   out_767115652523811832[23] = 0.0;
   out_767115652523811832[24] = 0.0;
   out_767115652523811832[25] = 0.0;
   out_767115652523811832[26] = 0.0;
   out_767115652523811832[27] = 0.0;
   out_767115652523811832[28] = 0.0;
   out_767115652523811832[29] = 0.0;
   out_767115652523811832[30] = 0.0;
   out_767115652523811832[31] = 0.0;
   out_767115652523811832[32] = 0.0;
   out_767115652523811832[33] = 0.0;
   out_767115652523811832[34] = 0.0;
   out_767115652523811832[35] = 0.0;
   out_767115652523811832[36] = 0.0;
   out_767115652523811832[37] = 0.0;
   out_767115652523811832[38] = 1.0;
   out_767115652523811832[39] = 0.0;
   out_767115652523811832[40] = 0.0;
   out_767115652523811832[41] = 0.0;
   out_767115652523811832[42] = 0.0;
   out_767115652523811832[43] = 0.0;
   out_767115652523811832[44] = 0.0;
   out_767115652523811832[45] = 0.0;
   out_767115652523811832[46] = 0.0;
   out_767115652523811832[47] = 0.0;
   out_767115652523811832[48] = 0.0;
   out_767115652523811832[49] = 0.0;
   out_767115652523811832[50] = 0.0;
   out_767115652523811832[51] = 0.0;
   out_767115652523811832[52] = 0.0;
   out_767115652523811832[53] = 0.0;
   out_767115652523811832[54] = 0.0;
   out_767115652523811832[55] = 0.0;
   out_767115652523811832[56] = 0.0;
   out_767115652523811832[57] = 1.0;
   out_767115652523811832[58] = 0.0;
   out_767115652523811832[59] = 0.0;
   out_767115652523811832[60] = 0.0;
   out_767115652523811832[61] = 0.0;
   out_767115652523811832[62] = 0.0;
   out_767115652523811832[63] = 0.0;
   out_767115652523811832[64] = 0.0;
   out_767115652523811832[65] = 0.0;
   out_767115652523811832[66] = 0.0;
   out_767115652523811832[67] = 0.0;
   out_767115652523811832[68] = 0.0;
   out_767115652523811832[69] = 0.0;
   out_767115652523811832[70] = 0.0;
   out_767115652523811832[71] = 0.0;
   out_767115652523811832[72] = 0.0;
   out_767115652523811832[73] = 0.0;
   out_767115652523811832[74] = 0.0;
   out_767115652523811832[75] = 0.0;
   out_767115652523811832[76] = 1.0;
   out_767115652523811832[77] = 0.0;
   out_767115652523811832[78] = 0.0;
   out_767115652523811832[79] = 0.0;
   out_767115652523811832[80] = 0.0;
   out_767115652523811832[81] = 0.0;
   out_767115652523811832[82] = 0.0;
   out_767115652523811832[83] = 0.0;
   out_767115652523811832[84] = 0.0;
   out_767115652523811832[85] = 0.0;
   out_767115652523811832[86] = 0.0;
   out_767115652523811832[87] = 0.0;
   out_767115652523811832[88] = 0.0;
   out_767115652523811832[89] = 0.0;
   out_767115652523811832[90] = 0.0;
   out_767115652523811832[91] = 0.0;
   out_767115652523811832[92] = 0.0;
   out_767115652523811832[93] = 0.0;
   out_767115652523811832[94] = 0.0;
   out_767115652523811832[95] = 1.0;
   out_767115652523811832[96] = 0.0;
   out_767115652523811832[97] = 0.0;
   out_767115652523811832[98] = 0.0;
   out_767115652523811832[99] = 0.0;
   out_767115652523811832[100] = 0.0;
   out_767115652523811832[101] = 0.0;
   out_767115652523811832[102] = 0.0;
   out_767115652523811832[103] = 0.0;
   out_767115652523811832[104] = 0.0;
   out_767115652523811832[105] = 0.0;
   out_767115652523811832[106] = 0.0;
   out_767115652523811832[107] = 0.0;
   out_767115652523811832[108] = 0.0;
   out_767115652523811832[109] = 0.0;
   out_767115652523811832[110] = 0.0;
   out_767115652523811832[111] = 0.0;
   out_767115652523811832[112] = 0.0;
   out_767115652523811832[113] = 0.0;
   out_767115652523811832[114] = 1.0;
   out_767115652523811832[115] = 0.0;
   out_767115652523811832[116] = 0.0;
   out_767115652523811832[117] = 0.0;
   out_767115652523811832[118] = 0.0;
   out_767115652523811832[119] = 0.0;
   out_767115652523811832[120] = 0.0;
   out_767115652523811832[121] = 0.0;
   out_767115652523811832[122] = 0.0;
   out_767115652523811832[123] = 0.0;
   out_767115652523811832[124] = 0.0;
   out_767115652523811832[125] = 0.0;
   out_767115652523811832[126] = 0.0;
   out_767115652523811832[127] = 0.0;
   out_767115652523811832[128] = 0.0;
   out_767115652523811832[129] = 0.0;
   out_767115652523811832[130] = 0.0;
   out_767115652523811832[131] = 0.0;
   out_767115652523811832[132] = 0.0;
   out_767115652523811832[133] = 1.0;
   out_767115652523811832[134] = 0.0;
   out_767115652523811832[135] = 0.0;
   out_767115652523811832[136] = 0.0;
   out_767115652523811832[137] = 0.0;
   out_767115652523811832[138] = 0.0;
   out_767115652523811832[139] = 0.0;
   out_767115652523811832[140] = 0.0;
   out_767115652523811832[141] = 0.0;
   out_767115652523811832[142] = 0.0;
   out_767115652523811832[143] = 0.0;
   out_767115652523811832[144] = 0.0;
   out_767115652523811832[145] = 0.0;
   out_767115652523811832[146] = 0.0;
   out_767115652523811832[147] = 0.0;
   out_767115652523811832[148] = 0.0;
   out_767115652523811832[149] = 0.0;
   out_767115652523811832[150] = 0.0;
   out_767115652523811832[151] = 0.0;
   out_767115652523811832[152] = 1.0;
   out_767115652523811832[153] = 0.0;
   out_767115652523811832[154] = 0.0;
   out_767115652523811832[155] = 0.0;
   out_767115652523811832[156] = 0.0;
   out_767115652523811832[157] = 0.0;
   out_767115652523811832[158] = 0.0;
   out_767115652523811832[159] = 0.0;
   out_767115652523811832[160] = 0.0;
   out_767115652523811832[161] = 0.0;
   out_767115652523811832[162] = 0.0;
   out_767115652523811832[163] = 0.0;
   out_767115652523811832[164] = 0.0;
   out_767115652523811832[165] = 0.0;
   out_767115652523811832[166] = 0.0;
   out_767115652523811832[167] = 0.0;
   out_767115652523811832[168] = 0.0;
   out_767115652523811832[169] = 0.0;
   out_767115652523811832[170] = 0.0;
   out_767115652523811832[171] = 1.0;
   out_767115652523811832[172] = 0.0;
   out_767115652523811832[173] = 0.0;
   out_767115652523811832[174] = 0.0;
   out_767115652523811832[175] = 0.0;
   out_767115652523811832[176] = 0.0;
   out_767115652523811832[177] = 0.0;
   out_767115652523811832[178] = 0.0;
   out_767115652523811832[179] = 0.0;
   out_767115652523811832[180] = 0.0;
   out_767115652523811832[181] = 0.0;
   out_767115652523811832[182] = 0.0;
   out_767115652523811832[183] = 0.0;
   out_767115652523811832[184] = 0.0;
   out_767115652523811832[185] = 0.0;
   out_767115652523811832[186] = 0.0;
   out_767115652523811832[187] = 0.0;
   out_767115652523811832[188] = 0.0;
   out_767115652523811832[189] = 0.0;
   out_767115652523811832[190] = 1.0;
   out_767115652523811832[191] = 0.0;
   out_767115652523811832[192] = 0.0;
   out_767115652523811832[193] = 0.0;
   out_767115652523811832[194] = 0.0;
   out_767115652523811832[195] = 0.0;
   out_767115652523811832[196] = 0.0;
   out_767115652523811832[197] = 0.0;
   out_767115652523811832[198] = 0.0;
   out_767115652523811832[199] = 0.0;
   out_767115652523811832[200] = 0.0;
   out_767115652523811832[201] = 0.0;
   out_767115652523811832[202] = 0.0;
   out_767115652523811832[203] = 0.0;
   out_767115652523811832[204] = 0.0;
   out_767115652523811832[205] = 0.0;
   out_767115652523811832[206] = 0.0;
   out_767115652523811832[207] = 0.0;
   out_767115652523811832[208] = 0.0;
   out_767115652523811832[209] = 1.0;
   out_767115652523811832[210] = 0.0;
   out_767115652523811832[211] = 0.0;
   out_767115652523811832[212] = 0.0;
   out_767115652523811832[213] = 0.0;
   out_767115652523811832[214] = 0.0;
   out_767115652523811832[215] = 0.0;
   out_767115652523811832[216] = 0.0;
   out_767115652523811832[217] = 0.0;
   out_767115652523811832[218] = 0.0;
   out_767115652523811832[219] = 0.0;
   out_767115652523811832[220] = 0.0;
   out_767115652523811832[221] = 0.0;
   out_767115652523811832[222] = 0.0;
   out_767115652523811832[223] = 0.0;
   out_767115652523811832[224] = 0.0;
   out_767115652523811832[225] = 0.0;
   out_767115652523811832[226] = 0.0;
   out_767115652523811832[227] = 0.0;
   out_767115652523811832[228] = 1.0;
   out_767115652523811832[229] = 0.0;
   out_767115652523811832[230] = 0.0;
   out_767115652523811832[231] = 0.0;
   out_767115652523811832[232] = 0.0;
   out_767115652523811832[233] = 0.0;
   out_767115652523811832[234] = 0.0;
   out_767115652523811832[235] = 0.0;
   out_767115652523811832[236] = 0.0;
   out_767115652523811832[237] = 0.0;
   out_767115652523811832[238] = 0.0;
   out_767115652523811832[239] = 0.0;
   out_767115652523811832[240] = 0.0;
   out_767115652523811832[241] = 0.0;
   out_767115652523811832[242] = 0.0;
   out_767115652523811832[243] = 0.0;
   out_767115652523811832[244] = 0.0;
   out_767115652523811832[245] = 0.0;
   out_767115652523811832[246] = 0.0;
   out_767115652523811832[247] = 1.0;
   out_767115652523811832[248] = 0.0;
   out_767115652523811832[249] = 0.0;
   out_767115652523811832[250] = 0.0;
   out_767115652523811832[251] = 0.0;
   out_767115652523811832[252] = 0.0;
   out_767115652523811832[253] = 0.0;
   out_767115652523811832[254] = 0.0;
   out_767115652523811832[255] = 0.0;
   out_767115652523811832[256] = 0.0;
   out_767115652523811832[257] = 0.0;
   out_767115652523811832[258] = 0.0;
   out_767115652523811832[259] = 0.0;
   out_767115652523811832[260] = 0.0;
   out_767115652523811832[261] = 0.0;
   out_767115652523811832[262] = 0.0;
   out_767115652523811832[263] = 0.0;
   out_767115652523811832[264] = 0.0;
   out_767115652523811832[265] = 0.0;
   out_767115652523811832[266] = 1.0;
   out_767115652523811832[267] = 0.0;
   out_767115652523811832[268] = 0.0;
   out_767115652523811832[269] = 0.0;
   out_767115652523811832[270] = 0.0;
   out_767115652523811832[271] = 0.0;
   out_767115652523811832[272] = 0.0;
   out_767115652523811832[273] = 0.0;
   out_767115652523811832[274] = 0.0;
   out_767115652523811832[275] = 0.0;
   out_767115652523811832[276] = 0.0;
   out_767115652523811832[277] = 0.0;
   out_767115652523811832[278] = 0.0;
   out_767115652523811832[279] = 0.0;
   out_767115652523811832[280] = 0.0;
   out_767115652523811832[281] = 0.0;
   out_767115652523811832[282] = 0.0;
   out_767115652523811832[283] = 0.0;
   out_767115652523811832[284] = 0.0;
   out_767115652523811832[285] = 1.0;
   out_767115652523811832[286] = 0.0;
   out_767115652523811832[287] = 0.0;
   out_767115652523811832[288] = 0.0;
   out_767115652523811832[289] = 0.0;
   out_767115652523811832[290] = 0.0;
   out_767115652523811832[291] = 0.0;
   out_767115652523811832[292] = 0.0;
   out_767115652523811832[293] = 0.0;
   out_767115652523811832[294] = 0.0;
   out_767115652523811832[295] = 0.0;
   out_767115652523811832[296] = 0.0;
   out_767115652523811832[297] = 0.0;
   out_767115652523811832[298] = 0.0;
   out_767115652523811832[299] = 0.0;
   out_767115652523811832[300] = 0.0;
   out_767115652523811832[301] = 0.0;
   out_767115652523811832[302] = 0.0;
   out_767115652523811832[303] = 0.0;
   out_767115652523811832[304] = 1.0;
   out_767115652523811832[305] = 0.0;
   out_767115652523811832[306] = 0.0;
   out_767115652523811832[307] = 0.0;
   out_767115652523811832[308] = 0.0;
   out_767115652523811832[309] = 0.0;
   out_767115652523811832[310] = 0.0;
   out_767115652523811832[311] = 0.0;
   out_767115652523811832[312] = 0.0;
   out_767115652523811832[313] = 0.0;
   out_767115652523811832[314] = 0.0;
   out_767115652523811832[315] = 0.0;
   out_767115652523811832[316] = 0.0;
   out_767115652523811832[317] = 0.0;
   out_767115652523811832[318] = 0.0;
   out_767115652523811832[319] = 0.0;
   out_767115652523811832[320] = 0.0;
   out_767115652523811832[321] = 0.0;
   out_767115652523811832[322] = 0.0;
   out_767115652523811832[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_5020659945953845111) {
   out_5020659945953845111[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_5020659945953845111[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_5020659945953845111[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_5020659945953845111[3] = dt*state[12] + state[3];
   out_5020659945953845111[4] = dt*state[13] + state[4];
   out_5020659945953845111[5] = dt*state[14] + state[5];
   out_5020659945953845111[6] = state[6];
   out_5020659945953845111[7] = state[7];
   out_5020659945953845111[8] = state[8];
   out_5020659945953845111[9] = state[9];
   out_5020659945953845111[10] = state[10];
   out_5020659945953845111[11] = state[11];
   out_5020659945953845111[12] = state[12];
   out_5020659945953845111[13] = state[13];
   out_5020659945953845111[14] = state[14];
   out_5020659945953845111[15] = state[15];
   out_5020659945953845111[16] = state[16];
   out_5020659945953845111[17] = state[17];
}
void F_fun(double *state, double dt, double *out_4864829611890037349) {
   out_4864829611890037349[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_4864829611890037349[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_4864829611890037349[2] = 0;
   out_4864829611890037349[3] = 0;
   out_4864829611890037349[4] = 0;
   out_4864829611890037349[5] = 0;
   out_4864829611890037349[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_4864829611890037349[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_4864829611890037349[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_4864829611890037349[9] = 0;
   out_4864829611890037349[10] = 0;
   out_4864829611890037349[11] = 0;
   out_4864829611890037349[12] = 0;
   out_4864829611890037349[13] = 0;
   out_4864829611890037349[14] = 0;
   out_4864829611890037349[15] = 0;
   out_4864829611890037349[16] = 0;
   out_4864829611890037349[17] = 0;
   out_4864829611890037349[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_4864829611890037349[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_4864829611890037349[20] = 0;
   out_4864829611890037349[21] = 0;
   out_4864829611890037349[22] = 0;
   out_4864829611890037349[23] = 0;
   out_4864829611890037349[24] = 0;
   out_4864829611890037349[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_4864829611890037349[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_4864829611890037349[27] = 0;
   out_4864829611890037349[28] = 0;
   out_4864829611890037349[29] = 0;
   out_4864829611890037349[30] = 0;
   out_4864829611890037349[31] = 0;
   out_4864829611890037349[32] = 0;
   out_4864829611890037349[33] = 0;
   out_4864829611890037349[34] = 0;
   out_4864829611890037349[35] = 0;
   out_4864829611890037349[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_4864829611890037349[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_4864829611890037349[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_4864829611890037349[39] = 0;
   out_4864829611890037349[40] = 0;
   out_4864829611890037349[41] = 0;
   out_4864829611890037349[42] = 0;
   out_4864829611890037349[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_4864829611890037349[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_4864829611890037349[45] = 0;
   out_4864829611890037349[46] = 0;
   out_4864829611890037349[47] = 0;
   out_4864829611890037349[48] = 0;
   out_4864829611890037349[49] = 0;
   out_4864829611890037349[50] = 0;
   out_4864829611890037349[51] = 0;
   out_4864829611890037349[52] = 0;
   out_4864829611890037349[53] = 0;
   out_4864829611890037349[54] = 0;
   out_4864829611890037349[55] = 0;
   out_4864829611890037349[56] = 0;
   out_4864829611890037349[57] = 1;
   out_4864829611890037349[58] = 0;
   out_4864829611890037349[59] = 0;
   out_4864829611890037349[60] = 0;
   out_4864829611890037349[61] = 0;
   out_4864829611890037349[62] = 0;
   out_4864829611890037349[63] = 0;
   out_4864829611890037349[64] = 0;
   out_4864829611890037349[65] = 0;
   out_4864829611890037349[66] = dt;
   out_4864829611890037349[67] = 0;
   out_4864829611890037349[68] = 0;
   out_4864829611890037349[69] = 0;
   out_4864829611890037349[70] = 0;
   out_4864829611890037349[71] = 0;
   out_4864829611890037349[72] = 0;
   out_4864829611890037349[73] = 0;
   out_4864829611890037349[74] = 0;
   out_4864829611890037349[75] = 0;
   out_4864829611890037349[76] = 1;
   out_4864829611890037349[77] = 0;
   out_4864829611890037349[78] = 0;
   out_4864829611890037349[79] = 0;
   out_4864829611890037349[80] = 0;
   out_4864829611890037349[81] = 0;
   out_4864829611890037349[82] = 0;
   out_4864829611890037349[83] = 0;
   out_4864829611890037349[84] = 0;
   out_4864829611890037349[85] = dt;
   out_4864829611890037349[86] = 0;
   out_4864829611890037349[87] = 0;
   out_4864829611890037349[88] = 0;
   out_4864829611890037349[89] = 0;
   out_4864829611890037349[90] = 0;
   out_4864829611890037349[91] = 0;
   out_4864829611890037349[92] = 0;
   out_4864829611890037349[93] = 0;
   out_4864829611890037349[94] = 0;
   out_4864829611890037349[95] = 1;
   out_4864829611890037349[96] = 0;
   out_4864829611890037349[97] = 0;
   out_4864829611890037349[98] = 0;
   out_4864829611890037349[99] = 0;
   out_4864829611890037349[100] = 0;
   out_4864829611890037349[101] = 0;
   out_4864829611890037349[102] = 0;
   out_4864829611890037349[103] = 0;
   out_4864829611890037349[104] = dt;
   out_4864829611890037349[105] = 0;
   out_4864829611890037349[106] = 0;
   out_4864829611890037349[107] = 0;
   out_4864829611890037349[108] = 0;
   out_4864829611890037349[109] = 0;
   out_4864829611890037349[110] = 0;
   out_4864829611890037349[111] = 0;
   out_4864829611890037349[112] = 0;
   out_4864829611890037349[113] = 0;
   out_4864829611890037349[114] = 1;
   out_4864829611890037349[115] = 0;
   out_4864829611890037349[116] = 0;
   out_4864829611890037349[117] = 0;
   out_4864829611890037349[118] = 0;
   out_4864829611890037349[119] = 0;
   out_4864829611890037349[120] = 0;
   out_4864829611890037349[121] = 0;
   out_4864829611890037349[122] = 0;
   out_4864829611890037349[123] = 0;
   out_4864829611890037349[124] = 0;
   out_4864829611890037349[125] = 0;
   out_4864829611890037349[126] = 0;
   out_4864829611890037349[127] = 0;
   out_4864829611890037349[128] = 0;
   out_4864829611890037349[129] = 0;
   out_4864829611890037349[130] = 0;
   out_4864829611890037349[131] = 0;
   out_4864829611890037349[132] = 0;
   out_4864829611890037349[133] = 1;
   out_4864829611890037349[134] = 0;
   out_4864829611890037349[135] = 0;
   out_4864829611890037349[136] = 0;
   out_4864829611890037349[137] = 0;
   out_4864829611890037349[138] = 0;
   out_4864829611890037349[139] = 0;
   out_4864829611890037349[140] = 0;
   out_4864829611890037349[141] = 0;
   out_4864829611890037349[142] = 0;
   out_4864829611890037349[143] = 0;
   out_4864829611890037349[144] = 0;
   out_4864829611890037349[145] = 0;
   out_4864829611890037349[146] = 0;
   out_4864829611890037349[147] = 0;
   out_4864829611890037349[148] = 0;
   out_4864829611890037349[149] = 0;
   out_4864829611890037349[150] = 0;
   out_4864829611890037349[151] = 0;
   out_4864829611890037349[152] = 1;
   out_4864829611890037349[153] = 0;
   out_4864829611890037349[154] = 0;
   out_4864829611890037349[155] = 0;
   out_4864829611890037349[156] = 0;
   out_4864829611890037349[157] = 0;
   out_4864829611890037349[158] = 0;
   out_4864829611890037349[159] = 0;
   out_4864829611890037349[160] = 0;
   out_4864829611890037349[161] = 0;
   out_4864829611890037349[162] = 0;
   out_4864829611890037349[163] = 0;
   out_4864829611890037349[164] = 0;
   out_4864829611890037349[165] = 0;
   out_4864829611890037349[166] = 0;
   out_4864829611890037349[167] = 0;
   out_4864829611890037349[168] = 0;
   out_4864829611890037349[169] = 0;
   out_4864829611890037349[170] = 0;
   out_4864829611890037349[171] = 1;
   out_4864829611890037349[172] = 0;
   out_4864829611890037349[173] = 0;
   out_4864829611890037349[174] = 0;
   out_4864829611890037349[175] = 0;
   out_4864829611890037349[176] = 0;
   out_4864829611890037349[177] = 0;
   out_4864829611890037349[178] = 0;
   out_4864829611890037349[179] = 0;
   out_4864829611890037349[180] = 0;
   out_4864829611890037349[181] = 0;
   out_4864829611890037349[182] = 0;
   out_4864829611890037349[183] = 0;
   out_4864829611890037349[184] = 0;
   out_4864829611890037349[185] = 0;
   out_4864829611890037349[186] = 0;
   out_4864829611890037349[187] = 0;
   out_4864829611890037349[188] = 0;
   out_4864829611890037349[189] = 0;
   out_4864829611890037349[190] = 1;
   out_4864829611890037349[191] = 0;
   out_4864829611890037349[192] = 0;
   out_4864829611890037349[193] = 0;
   out_4864829611890037349[194] = 0;
   out_4864829611890037349[195] = 0;
   out_4864829611890037349[196] = 0;
   out_4864829611890037349[197] = 0;
   out_4864829611890037349[198] = 0;
   out_4864829611890037349[199] = 0;
   out_4864829611890037349[200] = 0;
   out_4864829611890037349[201] = 0;
   out_4864829611890037349[202] = 0;
   out_4864829611890037349[203] = 0;
   out_4864829611890037349[204] = 0;
   out_4864829611890037349[205] = 0;
   out_4864829611890037349[206] = 0;
   out_4864829611890037349[207] = 0;
   out_4864829611890037349[208] = 0;
   out_4864829611890037349[209] = 1;
   out_4864829611890037349[210] = 0;
   out_4864829611890037349[211] = 0;
   out_4864829611890037349[212] = 0;
   out_4864829611890037349[213] = 0;
   out_4864829611890037349[214] = 0;
   out_4864829611890037349[215] = 0;
   out_4864829611890037349[216] = 0;
   out_4864829611890037349[217] = 0;
   out_4864829611890037349[218] = 0;
   out_4864829611890037349[219] = 0;
   out_4864829611890037349[220] = 0;
   out_4864829611890037349[221] = 0;
   out_4864829611890037349[222] = 0;
   out_4864829611890037349[223] = 0;
   out_4864829611890037349[224] = 0;
   out_4864829611890037349[225] = 0;
   out_4864829611890037349[226] = 0;
   out_4864829611890037349[227] = 0;
   out_4864829611890037349[228] = 1;
   out_4864829611890037349[229] = 0;
   out_4864829611890037349[230] = 0;
   out_4864829611890037349[231] = 0;
   out_4864829611890037349[232] = 0;
   out_4864829611890037349[233] = 0;
   out_4864829611890037349[234] = 0;
   out_4864829611890037349[235] = 0;
   out_4864829611890037349[236] = 0;
   out_4864829611890037349[237] = 0;
   out_4864829611890037349[238] = 0;
   out_4864829611890037349[239] = 0;
   out_4864829611890037349[240] = 0;
   out_4864829611890037349[241] = 0;
   out_4864829611890037349[242] = 0;
   out_4864829611890037349[243] = 0;
   out_4864829611890037349[244] = 0;
   out_4864829611890037349[245] = 0;
   out_4864829611890037349[246] = 0;
   out_4864829611890037349[247] = 1;
   out_4864829611890037349[248] = 0;
   out_4864829611890037349[249] = 0;
   out_4864829611890037349[250] = 0;
   out_4864829611890037349[251] = 0;
   out_4864829611890037349[252] = 0;
   out_4864829611890037349[253] = 0;
   out_4864829611890037349[254] = 0;
   out_4864829611890037349[255] = 0;
   out_4864829611890037349[256] = 0;
   out_4864829611890037349[257] = 0;
   out_4864829611890037349[258] = 0;
   out_4864829611890037349[259] = 0;
   out_4864829611890037349[260] = 0;
   out_4864829611890037349[261] = 0;
   out_4864829611890037349[262] = 0;
   out_4864829611890037349[263] = 0;
   out_4864829611890037349[264] = 0;
   out_4864829611890037349[265] = 0;
   out_4864829611890037349[266] = 1;
   out_4864829611890037349[267] = 0;
   out_4864829611890037349[268] = 0;
   out_4864829611890037349[269] = 0;
   out_4864829611890037349[270] = 0;
   out_4864829611890037349[271] = 0;
   out_4864829611890037349[272] = 0;
   out_4864829611890037349[273] = 0;
   out_4864829611890037349[274] = 0;
   out_4864829611890037349[275] = 0;
   out_4864829611890037349[276] = 0;
   out_4864829611890037349[277] = 0;
   out_4864829611890037349[278] = 0;
   out_4864829611890037349[279] = 0;
   out_4864829611890037349[280] = 0;
   out_4864829611890037349[281] = 0;
   out_4864829611890037349[282] = 0;
   out_4864829611890037349[283] = 0;
   out_4864829611890037349[284] = 0;
   out_4864829611890037349[285] = 1;
   out_4864829611890037349[286] = 0;
   out_4864829611890037349[287] = 0;
   out_4864829611890037349[288] = 0;
   out_4864829611890037349[289] = 0;
   out_4864829611890037349[290] = 0;
   out_4864829611890037349[291] = 0;
   out_4864829611890037349[292] = 0;
   out_4864829611890037349[293] = 0;
   out_4864829611890037349[294] = 0;
   out_4864829611890037349[295] = 0;
   out_4864829611890037349[296] = 0;
   out_4864829611890037349[297] = 0;
   out_4864829611890037349[298] = 0;
   out_4864829611890037349[299] = 0;
   out_4864829611890037349[300] = 0;
   out_4864829611890037349[301] = 0;
   out_4864829611890037349[302] = 0;
   out_4864829611890037349[303] = 0;
   out_4864829611890037349[304] = 1;
   out_4864829611890037349[305] = 0;
   out_4864829611890037349[306] = 0;
   out_4864829611890037349[307] = 0;
   out_4864829611890037349[308] = 0;
   out_4864829611890037349[309] = 0;
   out_4864829611890037349[310] = 0;
   out_4864829611890037349[311] = 0;
   out_4864829611890037349[312] = 0;
   out_4864829611890037349[313] = 0;
   out_4864829611890037349[314] = 0;
   out_4864829611890037349[315] = 0;
   out_4864829611890037349[316] = 0;
   out_4864829611890037349[317] = 0;
   out_4864829611890037349[318] = 0;
   out_4864829611890037349[319] = 0;
   out_4864829611890037349[320] = 0;
   out_4864829611890037349[321] = 0;
   out_4864829611890037349[322] = 0;
   out_4864829611890037349[323] = 1;
}
void h_4(double *state, double *unused, double *out_9073493681667836505) {
   out_9073493681667836505[0] = state[6] + state[9];
   out_9073493681667836505[1] = state[7] + state[10];
   out_9073493681667836505[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_8245868767389872660) {
   out_8245868767389872660[0] = 0;
   out_8245868767389872660[1] = 0;
   out_8245868767389872660[2] = 0;
   out_8245868767389872660[3] = 0;
   out_8245868767389872660[4] = 0;
   out_8245868767389872660[5] = 0;
   out_8245868767389872660[6] = 1;
   out_8245868767389872660[7] = 0;
   out_8245868767389872660[8] = 0;
   out_8245868767389872660[9] = 1;
   out_8245868767389872660[10] = 0;
   out_8245868767389872660[11] = 0;
   out_8245868767389872660[12] = 0;
   out_8245868767389872660[13] = 0;
   out_8245868767389872660[14] = 0;
   out_8245868767389872660[15] = 0;
   out_8245868767389872660[16] = 0;
   out_8245868767389872660[17] = 0;
   out_8245868767389872660[18] = 0;
   out_8245868767389872660[19] = 0;
   out_8245868767389872660[20] = 0;
   out_8245868767389872660[21] = 0;
   out_8245868767389872660[22] = 0;
   out_8245868767389872660[23] = 0;
   out_8245868767389872660[24] = 0;
   out_8245868767389872660[25] = 1;
   out_8245868767389872660[26] = 0;
   out_8245868767389872660[27] = 0;
   out_8245868767389872660[28] = 1;
   out_8245868767389872660[29] = 0;
   out_8245868767389872660[30] = 0;
   out_8245868767389872660[31] = 0;
   out_8245868767389872660[32] = 0;
   out_8245868767389872660[33] = 0;
   out_8245868767389872660[34] = 0;
   out_8245868767389872660[35] = 0;
   out_8245868767389872660[36] = 0;
   out_8245868767389872660[37] = 0;
   out_8245868767389872660[38] = 0;
   out_8245868767389872660[39] = 0;
   out_8245868767389872660[40] = 0;
   out_8245868767389872660[41] = 0;
   out_8245868767389872660[42] = 0;
   out_8245868767389872660[43] = 0;
   out_8245868767389872660[44] = 1;
   out_8245868767389872660[45] = 0;
   out_8245868767389872660[46] = 0;
   out_8245868767389872660[47] = 1;
   out_8245868767389872660[48] = 0;
   out_8245868767389872660[49] = 0;
   out_8245868767389872660[50] = 0;
   out_8245868767389872660[51] = 0;
   out_8245868767389872660[52] = 0;
   out_8245868767389872660[53] = 0;
}
void h_10(double *state, double *unused, double *out_7316638718416108353) {
   out_7316638718416108353[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_7316638718416108353[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_7316638718416108353[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_1400604490691219462) {
   out_1400604490691219462[0] = 0;
   out_1400604490691219462[1] = 9.8100000000000005*cos(state[1]);
   out_1400604490691219462[2] = 0;
   out_1400604490691219462[3] = 0;
   out_1400604490691219462[4] = -state[8];
   out_1400604490691219462[5] = state[7];
   out_1400604490691219462[6] = 0;
   out_1400604490691219462[7] = state[5];
   out_1400604490691219462[8] = -state[4];
   out_1400604490691219462[9] = 0;
   out_1400604490691219462[10] = 0;
   out_1400604490691219462[11] = 0;
   out_1400604490691219462[12] = 1;
   out_1400604490691219462[13] = 0;
   out_1400604490691219462[14] = 0;
   out_1400604490691219462[15] = 1;
   out_1400604490691219462[16] = 0;
   out_1400604490691219462[17] = 0;
   out_1400604490691219462[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_1400604490691219462[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_1400604490691219462[20] = 0;
   out_1400604490691219462[21] = state[8];
   out_1400604490691219462[22] = 0;
   out_1400604490691219462[23] = -state[6];
   out_1400604490691219462[24] = -state[5];
   out_1400604490691219462[25] = 0;
   out_1400604490691219462[26] = state[3];
   out_1400604490691219462[27] = 0;
   out_1400604490691219462[28] = 0;
   out_1400604490691219462[29] = 0;
   out_1400604490691219462[30] = 0;
   out_1400604490691219462[31] = 1;
   out_1400604490691219462[32] = 0;
   out_1400604490691219462[33] = 0;
   out_1400604490691219462[34] = 1;
   out_1400604490691219462[35] = 0;
   out_1400604490691219462[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_1400604490691219462[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_1400604490691219462[38] = 0;
   out_1400604490691219462[39] = -state[7];
   out_1400604490691219462[40] = state[6];
   out_1400604490691219462[41] = 0;
   out_1400604490691219462[42] = state[4];
   out_1400604490691219462[43] = -state[3];
   out_1400604490691219462[44] = 0;
   out_1400604490691219462[45] = 0;
   out_1400604490691219462[46] = 0;
   out_1400604490691219462[47] = 0;
   out_1400604490691219462[48] = 0;
   out_1400604490691219462[49] = 0;
   out_1400604490691219462[50] = 1;
   out_1400604490691219462[51] = 0;
   out_1400604490691219462[52] = 0;
   out_1400604490691219462[53] = 1;
}
void h_13(double *state, double *unused, double *out_3331559375824327691) {
   out_3331559375824327691[0] = state[3];
   out_3331559375824327691[1] = state[4];
   out_3331559375824327691[2] = state[5];
}
void H_13(double *state, double *unused, double *out_6988601480987346155) {
   out_6988601480987346155[0] = 0;
   out_6988601480987346155[1] = 0;
   out_6988601480987346155[2] = 0;
   out_6988601480987346155[3] = 1;
   out_6988601480987346155[4] = 0;
   out_6988601480987346155[5] = 0;
   out_6988601480987346155[6] = 0;
   out_6988601480987346155[7] = 0;
   out_6988601480987346155[8] = 0;
   out_6988601480987346155[9] = 0;
   out_6988601480987346155[10] = 0;
   out_6988601480987346155[11] = 0;
   out_6988601480987346155[12] = 0;
   out_6988601480987346155[13] = 0;
   out_6988601480987346155[14] = 0;
   out_6988601480987346155[15] = 0;
   out_6988601480987346155[16] = 0;
   out_6988601480987346155[17] = 0;
   out_6988601480987346155[18] = 0;
   out_6988601480987346155[19] = 0;
   out_6988601480987346155[20] = 0;
   out_6988601480987346155[21] = 0;
   out_6988601480987346155[22] = 1;
   out_6988601480987346155[23] = 0;
   out_6988601480987346155[24] = 0;
   out_6988601480987346155[25] = 0;
   out_6988601480987346155[26] = 0;
   out_6988601480987346155[27] = 0;
   out_6988601480987346155[28] = 0;
   out_6988601480987346155[29] = 0;
   out_6988601480987346155[30] = 0;
   out_6988601480987346155[31] = 0;
   out_6988601480987346155[32] = 0;
   out_6988601480987346155[33] = 0;
   out_6988601480987346155[34] = 0;
   out_6988601480987346155[35] = 0;
   out_6988601480987346155[36] = 0;
   out_6988601480987346155[37] = 0;
   out_6988601480987346155[38] = 0;
   out_6988601480987346155[39] = 0;
   out_6988601480987346155[40] = 0;
   out_6988601480987346155[41] = 1;
   out_6988601480987346155[42] = 0;
   out_6988601480987346155[43] = 0;
   out_6988601480987346155[44] = 0;
   out_6988601480987346155[45] = 0;
   out_6988601480987346155[46] = 0;
   out_6988601480987346155[47] = 0;
   out_6988601480987346155[48] = 0;
   out_6988601480987346155[49] = 0;
   out_6988601480987346155[50] = 0;
   out_6988601480987346155[51] = 0;
   out_6988601480987346155[52] = 0;
   out_6988601480987346155[53] = 0;
}
void h_14(double *state, double *unused, double *out_5696773055453158029) {
   out_5696773055453158029[0] = state[6];
   out_5696773055453158029[1] = state[7];
   out_5696773055453158029[2] = state[8];
}
void H_14(double *state, double *unused, double *out_6237634449980194427) {
   out_6237634449980194427[0] = 0;
   out_6237634449980194427[1] = 0;
   out_6237634449980194427[2] = 0;
   out_6237634449980194427[3] = 0;
   out_6237634449980194427[4] = 0;
   out_6237634449980194427[5] = 0;
   out_6237634449980194427[6] = 1;
   out_6237634449980194427[7] = 0;
   out_6237634449980194427[8] = 0;
   out_6237634449980194427[9] = 0;
   out_6237634449980194427[10] = 0;
   out_6237634449980194427[11] = 0;
   out_6237634449980194427[12] = 0;
   out_6237634449980194427[13] = 0;
   out_6237634449980194427[14] = 0;
   out_6237634449980194427[15] = 0;
   out_6237634449980194427[16] = 0;
   out_6237634449980194427[17] = 0;
   out_6237634449980194427[18] = 0;
   out_6237634449980194427[19] = 0;
   out_6237634449980194427[20] = 0;
   out_6237634449980194427[21] = 0;
   out_6237634449980194427[22] = 0;
   out_6237634449980194427[23] = 0;
   out_6237634449980194427[24] = 0;
   out_6237634449980194427[25] = 1;
   out_6237634449980194427[26] = 0;
   out_6237634449980194427[27] = 0;
   out_6237634449980194427[28] = 0;
   out_6237634449980194427[29] = 0;
   out_6237634449980194427[30] = 0;
   out_6237634449980194427[31] = 0;
   out_6237634449980194427[32] = 0;
   out_6237634449980194427[33] = 0;
   out_6237634449980194427[34] = 0;
   out_6237634449980194427[35] = 0;
   out_6237634449980194427[36] = 0;
   out_6237634449980194427[37] = 0;
   out_6237634449980194427[38] = 0;
   out_6237634449980194427[39] = 0;
   out_6237634449980194427[40] = 0;
   out_6237634449980194427[41] = 0;
   out_6237634449980194427[42] = 0;
   out_6237634449980194427[43] = 0;
   out_6237634449980194427[44] = 1;
   out_6237634449980194427[45] = 0;
   out_6237634449980194427[46] = 0;
   out_6237634449980194427[47] = 0;
   out_6237634449980194427[48] = 0;
   out_6237634449980194427[49] = 0;
   out_6237634449980194427[50] = 0;
   out_6237634449980194427[51] = 0;
   out_6237634449980194427[52] = 0;
   out_6237634449980194427[53] = 0;
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

void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_4, H_4, NULL, in_z, in_R, in_ea, MAHA_THRESH_4);
}
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_10, H_10, NULL, in_z, in_R, in_ea, MAHA_THRESH_10);
}
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_13, H_13, NULL, in_z, in_R, in_ea, MAHA_THRESH_13);
}
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_14, H_14, NULL, in_z, in_R, in_ea, MAHA_THRESH_14);
}
void pose_err_fun(double *nom_x, double *delta_x, double *out_5703369478197646593) {
  err_fun(nom_x, delta_x, out_5703369478197646593);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_8101996954815542477) {
  inv_err_fun(nom_x, true_x, out_8101996954815542477);
}
void pose_H_mod_fun(double *state, double *out_767115652523811832) {
  H_mod_fun(state, out_767115652523811832);
}
void pose_f_fun(double *state, double dt, double *out_5020659945953845111) {
  f_fun(state,  dt, out_5020659945953845111);
}
void pose_F_fun(double *state, double dt, double *out_4864829611890037349) {
  F_fun(state,  dt, out_4864829611890037349);
}
void pose_h_4(double *state, double *unused, double *out_9073493681667836505) {
  h_4(state, unused, out_9073493681667836505);
}
void pose_H_4(double *state, double *unused, double *out_8245868767389872660) {
  H_4(state, unused, out_8245868767389872660);
}
void pose_h_10(double *state, double *unused, double *out_7316638718416108353) {
  h_10(state, unused, out_7316638718416108353);
}
void pose_H_10(double *state, double *unused, double *out_1400604490691219462) {
  H_10(state, unused, out_1400604490691219462);
}
void pose_h_13(double *state, double *unused, double *out_3331559375824327691) {
  h_13(state, unused, out_3331559375824327691);
}
void pose_H_13(double *state, double *unused, double *out_6988601480987346155) {
  H_13(state, unused, out_6988601480987346155);
}
void pose_h_14(double *state, double *unused, double *out_5696773055453158029) {
  h_14(state, unused, out_5696773055453158029);
}
void pose_H_14(double *state, double *unused, double *out_6237634449980194427) {
  H_14(state, unused, out_6237634449980194427);
}
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt) {
  predict(in_x, in_P, in_Q, dt);
}
}

const EKF pose = {
  .name = "pose",
  .kinds = { 4, 10, 13, 14 },
  .feature_kinds = {  },
  .f_fun = pose_f_fun,
  .F_fun = pose_F_fun,
  .err_fun = pose_err_fun,
  .inv_err_fun = pose_inv_err_fun,
  .H_mod_fun = pose_H_mod_fun,
  .predict = pose_predict,
  .hs = {
    { 4, pose_h_4 },
    { 10, pose_h_10 },
    { 13, pose_h_13 },
    { 14, pose_h_14 },
  },
  .Hs = {
    { 4, pose_H_4 },
    { 10, pose_H_10 },
    { 13, pose_H_13 },
    { 14, pose_H_14 },
  },
  .updates = {
    { 4, pose_update_4 },
    { 10, pose_update_10 },
    { 13, pose_update_13 },
    { 14, pose_update_14 },
  },
  .Hes = {
  },
  .sets = {
  },
  .extra_routines = {
  },
};

ekf_lib_init(pose)
