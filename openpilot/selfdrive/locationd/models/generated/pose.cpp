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
void err_fun(double *nom_x, double *delta_x, double *out_376508033301991984) {
   out_376508033301991984[0] = delta_x[0] + nom_x[0];
   out_376508033301991984[1] = delta_x[1] + nom_x[1];
   out_376508033301991984[2] = delta_x[2] + nom_x[2];
   out_376508033301991984[3] = delta_x[3] + nom_x[3];
   out_376508033301991984[4] = delta_x[4] + nom_x[4];
   out_376508033301991984[5] = delta_x[5] + nom_x[5];
   out_376508033301991984[6] = delta_x[6] + nom_x[6];
   out_376508033301991984[7] = delta_x[7] + nom_x[7];
   out_376508033301991984[8] = delta_x[8] + nom_x[8];
   out_376508033301991984[9] = delta_x[9] + nom_x[9];
   out_376508033301991984[10] = delta_x[10] + nom_x[10];
   out_376508033301991984[11] = delta_x[11] + nom_x[11];
   out_376508033301991984[12] = delta_x[12] + nom_x[12];
   out_376508033301991984[13] = delta_x[13] + nom_x[13];
   out_376508033301991984[14] = delta_x[14] + nom_x[14];
   out_376508033301991984[15] = delta_x[15] + nom_x[15];
   out_376508033301991984[16] = delta_x[16] + nom_x[16];
   out_376508033301991984[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_3293932473507733269) {
   out_3293932473507733269[0] = -nom_x[0] + true_x[0];
   out_3293932473507733269[1] = -nom_x[1] + true_x[1];
   out_3293932473507733269[2] = -nom_x[2] + true_x[2];
   out_3293932473507733269[3] = -nom_x[3] + true_x[3];
   out_3293932473507733269[4] = -nom_x[4] + true_x[4];
   out_3293932473507733269[5] = -nom_x[5] + true_x[5];
   out_3293932473507733269[6] = -nom_x[6] + true_x[6];
   out_3293932473507733269[7] = -nom_x[7] + true_x[7];
   out_3293932473507733269[8] = -nom_x[8] + true_x[8];
   out_3293932473507733269[9] = -nom_x[9] + true_x[9];
   out_3293932473507733269[10] = -nom_x[10] + true_x[10];
   out_3293932473507733269[11] = -nom_x[11] + true_x[11];
   out_3293932473507733269[12] = -nom_x[12] + true_x[12];
   out_3293932473507733269[13] = -nom_x[13] + true_x[13];
   out_3293932473507733269[14] = -nom_x[14] + true_x[14];
   out_3293932473507733269[15] = -nom_x[15] + true_x[15];
   out_3293932473507733269[16] = -nom_x[16] + true_x[16];
   out_3293932473507733269[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_8189327656671088405) {
   out_8189327656671088405[0] = 1.0;
   out_8189327656671088405[1] = 0.0;
   out_8189327656671088405[2] = 0.0;
   out_8189327656671088405[3] = 0.0;
   out_8189327656671088405[4] = 0.0;
   out_8189327656671088405[5] = 0.0;
   out_8189327656671088405[6] = 0.0;
   out_8189327656671088405[7] = 0.0;
   out_8189327656671088405[8] = 0.0;
   out_8189327656671088405[9] = 0.0;
   out_8189327656671088405[10] = 0.0;
   out_8189327656671088405[11] = 0.0;
   out_8189327656671088405[12] = 0.0;
   out_8189327656671088405[13] = 0.0;
   out_8189327656671088405[14] = 0.0;
   out_8189327656671088405[15] = 0.0;
   out_8189327656671088405[16] = 0.0;
   out_8189327656671088405[17] = 0.0;
   out_8189327656671088405[18] = 0.0;
   out_8189327656671088405[19] = 1.0;
   out_8189327656671088405[20] = 0.0;
   out_8189327656671088405[21] = 0.0;
   out_8189327656671088405[22] = 0.0;
   out_8189327656671088405[23] = 0.0;
   out_8189327656671088405[24] = 0.0;
   out_8189327656671088405[25] = 0.0;
   out_8189327656671088405[26] = 0.0;
   out_8189327656671088405[27] = 0.0;
   out_8189327656671088405[28] = 0.0;
   out_8189327656671088405[29] = 0.0;
   out_8189327656671088405[30] = 0.0;
   out_8189327656671088405[31] = 0.0;
   out_8189327656671088405[32] = 0.0;
   out_8189327656671088405[33] = 0.0;
   out_8189327656671088405[34] = 0.0;
   out_8189327656671088405[35] = 0.0;
   out_8189327656671088405[36] = 0.0;
   out_8189327656671088405[37] = 0.0;
   out_8189327656671088405[38] = 1.0;
   out_8189327656671088405[39] = 0.0;
   out_8189327656671088405[40] = 0.0;
   out_8189327656671088405[41] = 0.0;
   out_8189327656671088405[42] = 0.0;
   out_8189327656671088405[43] = 0.0;
   out_8189327656671088405[44] = 0.0;
   out_8189327656671088405[45] = 0.0;
   out_8189327656671088405[46] = 0.0;
   out_8189327656671088405[47] = 0.0;
   out_8189327656671088405[48] = 0.0;
   out_8189327656671088405[49] = 0.0;
   out_8189327656671088405[50] = 0.0;
   out_8189327656671088405[51] = 0.0;
   out_8189327656671088405[52] = 0.0;
   out_8189327656671088405[53] = 0.0;
   out_8189327656671088405[54] = 0.0;
   out_8189327656671088405[55] = 0.0;
   out_8189327656671088405[56] = 0.0;
   out_8189327656671088405[57] = 1.0;
   out_8189327656671088405[58] = 0.0;
   out_8189327656671088405[59] = 0.0;
   out_8189327656671088405[60] = 0.0;
   out_8189327656671088405[61] = 0.0;
   out_8189327656671088405[62] = 0.0;
   out_8189327656671088405[63] = 0.0;
   out_8189327656671088405[64] = 0.0;
   out_8189327656671088405[65] = 0.0;
   out_8189327656671088405[66] = 0.0;
   out_8189327656671088405[67] = 0.0;
   out_8189327656671088405[68] = 0.0;
   out_8189327656671088405[69] = 0.0;
   out_8189327656671088405[70] = 0.0;
   out_8189327656671088405[71] = 0.0;
   out_8189327656671088405[72] = 0.0;
   out_8189327656671088405[73] = 0.0;
   out_8189327656671088405[74] = 0.0;
   out_8189327656671088405[75] = 0.0;
   out_8189327656671088405[76] = 1.0;
   out_8189327656671088405[77] = 0.0;
   out_8189327656671088405[78] = 0.0;
   out_8189327656671088405[79] = 0.0;
   out_8189327656671088405[80] = 0.0;
   out_8189327656671088405[81] = 0.0;
   out_8189327656671088405[82] = 0.0;
   out_8189327656671088405[83] = 0.0;
   out_8189327656671088405[84] = 0.0;
   out_8189327656671088405[85] = 0.0;
   out_8189327656671088405[86] = 0.0;
   out_8189327656671088405[87] = 0.0;
   out_8189327656671088405[88] = 0.0;
   out_8189327656671088405[89] = 0.0;
   out_8189327656671088405[90] = 0.0;
   out_8189327656671088405[91] = 0.0;
   out_8189327656671088405[92] = 0.0;
   out_8189327656671088405[93] = 0.0;
   out_8189327656671088405[94] = 0.0;
   out_8189327656671088405[95] = 1.0;
   out_8189327656671088405[96] = 0.0;
   out_8189327656671088405[97] = 0.0;
   out_8189327656671088405[98] = 0.0;
   out_8189327656671088405[99] = 0.0;
   out_8189327656671088405[100] = 0.0;
   out_8189327656671088405[101] = 0.0;
   out_8189327656671088405[102] = 0.0;
   out_8189327656671088405[103] = 0.0;
   out_8189327656671088405[104] = 0.0;
   out_8189327656671088405[105] = 0.0;
   out_8189327656671088405[106] = 0.0;
   out_8189327656671088405[107] = 0.0;
   out_8189327656671088405[108] = 0.0;
   out_8189327656671088405[109] = 0.0;
   out_8189327656671088405[110] = 0.0;
   out_8189327656671088405[111] = 0.0;
   out_8189327656671088405[112] = 0.0;
   out_8189327656671088405[113] = 0.0;
   out_8189327656671088405[114] = 1.0;
   out_8189327656671088405[115] = 0.0;
   out_8189327656671088405[116] = 0.0;
   out_8189327656671088405[117] = 0.0;
   out_8189327656671088405[118] = 0.0;
   out_8189327656671088405[119] = 0.0;
   out_8189327656671088405[120] = 0.0;
   out_8189327656671088405[121] = 0.0;
   out_8189327656671088405[122] = 0.0;
   out_8189327656671088405[123] = 0.0;
   out_8189327656671088405[124] = 0.0;
   out_8189327656671088405[125] = 0.0;
   out_8189327656671088405[126] = 0.0;
   out_8189327656671088405[127] = 0.0;
   out_8189327656671088405[128] = 0.0;
   out_8189327656671088405[129] = 0.0;
   out_8189327656671088405[130] = 0.0;
   out_8189327656671088405[131] = 0.0;
   out_8189327656671088405[132] = 0.0;
   out_8189327656671088405[133] = 1.0;
   out_8189327656671088405[134] = 0.0;
   out_8189327656671088405[135] = 0.0;
   out_8189327656671088405[136] = 0.0;
   out_8189327656671088405[137] = 0.0;
   out_8189327656671088405[138] = 0.0;
   out_8189327656671088405[139] = 0.0;
   out_8189327656671088405[140] = 0.0;
   out_8189327656671088405[141] = 0.0;
   out_8189327656671088405[142] = 0.0;
   out_8189327656671088405[143] = 0.0;
   out_8189327656671088405[144] = 0.0;
   out_8189327656671088405[145] = 0.0;
   out_8189327656671088405[146] = 0.0;
   out_8189327656671088405[147] = 0.0;
   out_8189327656671088405[148] = 0.0;
   out_8189327656671088405[149] = 0.0;
   out_8189327656671088405[150] = 0.0;
   out_8189327656671088405[151] = 0.0;
   out_8189327656671088405[152] = 1.0;
   out_8189327656671088405[153] = 0.0;
   out_8189327656671088405[154] = 0.0;
   out_8189327656671088405[155] = 0.0;
   out_8189327656671088405[156] = 0.0;
   out_8189327656671088405[157] = 0.0;
   out_8189327656671088405[158] = 0.0;
   out_8189327656671088405[159] = 0.0;
   out_8189327656671088405[160] = 0.0;
   out_8189327656671088405[161] = 0.0;
   out_8189327656671088405[162] = 0.0;
   out_8189327656671088405[163] = 0.0;
   out_8189327656671088405[164] = 0.0;
   out_8189327656671088405[165] = 0.0;
   out_8189327656671088405[166] = 0.0;
   out_8189327656671088405[167] = 0.0;
   out_8189327656671088405[168] = 0.0;
   out_8189327656671088405[169] = 0.0;
   out_8189327656671088405[170] = 0.0;
   out_8189327656671088405[171] = 1.0;
   out_8189327656671088405[172] = 0.0;
   out_8189327656671088405[173] = 0.0;
   out_8189327656671088405[174] = 0.0;
   out_8189327656671088405[175] = 0.0;
   out_8189327656671088405[176] = 0.0;
   out_8189327656671088405[177] = 0.0;
   out_8189327656671088405[178] = 0.0;
   out_8189327656671088405[179] = 0.0;
   out_8189327656671088405[180] = 0.0;
   out_8189327656671088405[181] = 0.0;
   out_8189327656671088405[182] = 0.0;
   out_8189327656671088405[183] = 0.0;
   out_8189327656671088405[184] = 0.0;
   out_8189327656671088405[185] = 0.0;
   out_8189327656671088405[186] = 0.0;
   out_8189327656671088405[187] = 0.0;
   out_8189327656671088405[188] = 0.0;
   out_8189327656671088405[189] = 0.0;
   out_8189327656671088405[190] = 1.0;
   out_8189327656671088405[191] = 0.0;
   out_8189327656671088405[192] = 0.0;
   out_8189327656671088405[193] = 0.0;
   out_8189327656671088405[194] = 0.0;
   out_8189327656671088405[195] = 0.0;
   out_8189327656671088405[196] = 0.0;
   out_8189327656671088405[197] = 0.0;
   out_8189327656671088405[198] = 0.0;
   out_8189327656671088405[199] = 0.0;
   out_8189327656671088405[200] = 0.0;
   out_8189327656671088405[201] = 0.0;
   out_8189327656671088405[202] = 0.0;
   out_8189327656671088405[203] = 0.0;
   out_8189327656671088405[204] = 0.0;
   out_8189327656671088405[205] = 0.0;
   out_8189327656671088405[206] = 0.0;
   out_8189327656671088405[207] = 0.0;
   out_8189327656671088405[208] = 0.0;
   out_8189327656671088405[209] = 1.0;
   out_8189327656671088405[210] = 0.0;
   out_8189327656671088405[211] = 0.0;
   out_8189327656671088405[212] = 0.0;
   out_8189327656671088405[213] = 0.0;
   out_8189327656671088405[214] = 0.0;
   out_8189327656671088405[215] = 0.0;
   out_8189327656671088405[216] = 0.0;
   out_8189327656671088405[217] = 0.0;
   out_8189327656671088405[218] = 0.0;
   out_8189327656671088405[219] = 0.0;
   out_8189327656671088405[220] = 0.0;
   out_8189327656671088405[221] = 0.0;
   out_8189327656671088405[222] = 0.0;
   out_8189327656671088405[223] = 0.0;
   out_8189327656671088405[224] = 0.0;
   out_8189327656671088405[225] = 0.0;
   out_8189327656671088405[226] = 0.0;
   out_8189327656671088405[227] = 0.0;
   out_8189327656671088405[228] = 1.0;
   out_8189327656671088405[229] = 0.0;
   out_8189327656671088405[230] = 0.0;
   out_8189327656671088405[231] = 0.0;
   out_8189327656671088405[232] = 0.0;
   out_8189327656671088405[233] = 0.0;
   out_8189327656671088405[234] = 0.0;
   out_8189327656671088405[235] = 0.0;
   out_8189327656671088405[236] = 0.0;
   out_8189327656671088405[237] = 0.0;
   out_8189327656671088405[238] = 0.0;
   out_8189327656671088405[239] = 0.0;
   out_8189327656671088405[240] = 0.0;
   out_8189327656671088405[241] = 0.0;
   out_8189327656671088405[242] = 0.0;
   out_8189327656671088405[243] = 0.0;
   out_8189327656671088405[244] = 0.0;
   out_8189327656671088405[245] = 0.0;
   out_8189327656671088405[246] = 0.0;
   out_8189327656671088405[247] = 1.0;
   out_8189327656671088405[248] = 0.0;
   out_8189327656671088405[249] = 0.0;
   out_8189327656671088405[250] = 0.0;
   out_8189327656671088405[251] = 0.0;
   out_8189327656671088405[252] = 0.0;
   out_8189327656671088405[253] = 0.0;
   out_8189327656671088405[254] = 0.0;
   out_8189327656671088405[255] = 0.0;
   out_8189327656671088405[256] = 0.0;
   out_8189327656671088405[257] = 0.0;
   out_8189327656671088405[258] = 0.0;
   out_8189327656671088405[259] = 0.0;
   out_8189327656671088405[260] = 0.0;
   out_8189327656671088405[261] = 0.0;
   out_8189327656671088405[262] = 0.0;
   out_8189327656671088405[263] = 0.0;
   out_8189327656671088405[264] = 0.0;
   out_8189327656671088405[265] = 0.0;
   out_8189327656671088405[266] = 1.0;
   out_8189327656671088405[267] = 0.0;
   out_8189327656671088405[268] = 0.0;
   out_8189327656671088405[269] = 0.0;
   out_8189327656671088405[270] = 0.0;
   out_8189327656671088405[271] = 0.0;
   out_8189327656671088405[272] = 0.0;
   out_8189327656671088405[273] = 0.0;
   out_8189327656671088405[274] = 0.0;
   out_8189327656671088405[275] = 0.0;
   out_8189327656671088405[276] = 0.0;
   out_8189327656671088405[277] = 0.0;
   out_8189327656671088405[278] = 0.0;
   out_8189327656671088405[279] = 0.0;
   out_8189327656671088405[280] = 0.0;
   out_8189327656671088405[281] = 0.0;
   out_8189327656671088405[282] = 0.0;
   out_8189327656671088405[283] = 0.0;
   out_8189327656671088405[284] = 0.0;
   out_8189327656671088405[285] = 1.0;
   out_8189327656671088405[286] = 0.0;
   out_8189327656671088405[287] = 0.0;
   out_8189327656671088405[288] = 0.0;
   out_8189327656671088405[289] = 0.0;
   out_8189327656671088405[290] = 0.0;
   out_8189327656671088405[291] = 0.0;
   out_8189327656671088405[292] = 0.0;
   out_8189327656671088405[293] = 0.0;
   out_8189327656671088405[294] = 0.0;
   out_8189327656671088405[295] = 0.0;
   out_8189327656671088405[296] = 0.0;
   out_8189327656671088405[297] = 0.0;
   out_8189327656671088405[298] = 0.0;
   out_8189327656671088405[299] = 0.0;
   out_8189327656671088405[300] = 0.0;
   out_8189327656671088405[301] = 0.0;
   out_8189327656671088405[302] = 0.0;
   out_8189327656671088405[303] = 0.0;
   out_8189327656671088405[304] = 1.0;
   out_8189327656671088405[305] = 0.0;
   out_8189327656671088405[306] = 0.0;
   out_8189327656671088405[307] = 0.0;
   out_8189327656671088405[308] = 0.0;
   out_8189327656671088405[309] = 0.0;
   out_8189327656671088405[310] = 0.0;
   out_8189327656671088405[311] = 0.0;
   out_8189327656671088405[312] = 0.0;
   out_8189327656671088405[313] = 0.0;
   out_8189327656671088405[314] = 0.0;
   out_8189327656671088405[315] = 0.0;
   out_8189327656671088405[316] = 0.0;
   out_8189327656671088405[317] = 0.0;
   out_8189327656671088405[318] = 0.0;
   out_8189327656671088405[319] = 0.0;
   out_8189327656671088405[320] = 0.0;
   out_8189327656671088405[321] = 0.0;
   out_8189327656671088405[322] = 0.0;
   out_8189327656671088405[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_7345618461576849553) {
   out_7345618461576849553[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_7345618461576849553[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_7345618461576849553[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_7345618461576849553[3] = dt*state[12] + state[3];
   out_7345618461576849553[4] = dt*state[13] + state[4];
   out_7345618461576849553[5] = dt*state[14] + state[5];
   out_7345618461576849553[6] = state[6];
   out_7345618461576849553[7] = state[7];
   out_7345618461576849553[8] = state[8];
   out_7345618461576849553[9] = state[9];
   out_7345618461576849553[10] = state[10];
   out_7345618461576849553[11] = state[11];
   out_7345618461576849553[12] = state[12];
   out_7345618461576849553[13] = state[13];
   out_7345618461576849553[14] = state[14];
   out_7345618461576849553[15] = state[15];
   out_7345618461576849553[16] = state[16];
   out_7345618461576849553[17] = state[17];
}
void F_fun(double *state, double dt, double *out_1545513811425739451) {
   out_1545513811425739451[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_1545513811425739451[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_1545513811425739451[2] = 0;
   out_1545513811425739451[3] = 0;
   out_1545513811425739451[4] = 0;
   out_1545513811425739451[5] = 0;
   out_1545513811425739451[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_1545513811425739451[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_1545513811425739451[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_1545513811425739451[9] = 0;
   out_1545513811425739451[10] = 0;
   out_1545513811425739451[11] = 0;
   out_1545513811425739451[12] = 0;
   out_1545513811425739451[13] = 0;
   out_1545513811425739451[14] = 0;
   out_1545513811425739451[15] = 0;
   out_1545513811425739451[16] = 0;
   out_1545513811425739451[17] = 0;
   out_1545513811425739451[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_1545513811425739451[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_1545513811425739451[20] = 0;
   out_1545513811425739451[21] = 0;
   out_1545513811425739451[22] = 0;
   out_1545513811425739451[23] = 0;
   out_1545513811425739451[24] = 0;
   out_1545513811425739451[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_1545513811425739451[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_1545513811425739451[27] = 0;
   out_1545513811425739451[28] = 0;
   out_1545513811425739451[29] = 0;
   out_1545513811425739451[30] = 0;
   out_1545513811425739451[31] = 0;
   out_1545513811425739451[32] = 0;
   out_1545513811425739451[33] = 0;
   out_1545513811425739451[34] = 0;
   out_1545513811425739451[35] = 0;
   out_1545513811425739451[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_1545513811425739451[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_1545513811425739451[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_1545513811425739451[39] = 0;
   out_1545513811425739451[40] = 0;
   out_1545513811425739451[41] = 0;
   out_1545513811425739451[42] = 0;
   out_1545513811425739451[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_1545513811425739451[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_1545513811425739451[45] = 0;
   out_1545513811425739451[46] = 0;
   out_1545513811425739451[47] = 0;
   out_1545513811425739451[48] = 0;
   out_1545513811425739451[49] = 0;
   out_1545513811425739451[50] = 0;
   out_1545513811425739451[51] = 0;
   out_1545513811425739451[52] = 0;
   out_1545513811425739451[53] = 0;
   out_1545513811425739451[54] = 0;
   out_1545513811425739451[55] = 0;
   out_1545513811425739451[56] = 0;
   out_1545513811425739451[57] = 1;
   out_1545513811425739451[58] = 0;
   out_1545513811425739451[59] = 0;
   out_1545513811425739451[60] = 0;
   out_1545513811425739451[61] = 0;
   out_1545513811425739451[62] = 0;
   out_1545513811425739451[63] = 0;
   out_1545513811425739451[64] = 0;
   out_1545513811425739451[65] = 0;
   out_1545513811425739451[66] = dt;
   out_1545513811425739451[67] = 0;
   out_1545513811425739451[68] = 0;
   out_1545513811425739451[69] = 0;
   out_1545513811425739451[70] = 0;
   out_1545513811425739451[71] = 0;
   out_1545513811425739451[72] = 0;
   out_1545513811425739451[73] = 0;
   out_1545513811425739451[74] = 0;
   out_1545513811425739451[75] = 0;
   out_1545513811425739451[76] = 1;
   out_1545513811425739451[77] = 0;
   out_1545513811425739451[78] = 0;
   out_1545513811425739451[79] = 0;
   out_1545513811425739451[80] = 0;
   out_1545513811425739451[81] = 0;
   out_1545513811425739451[82] = 0;
   out_1545513811425739451[83] = 0;
   out_1545513811425739451[84] = 0;
   out_1545513811425739451[85] = dt;
   out_1545513811425739451[86] = 0;
   out_1545513811425739451[87] = 0;
   out_1545513811425739451[88] = 0;
   out_1545513811425739451[89] = 0;
   out_1545513811425739451[90] = 0;
   out_1545513811425739451[91] = 0;
   out_1545513811425739451[92] = 0;
   out_1545513811425739451[93] = 0;
   out_1545513811425739451[94] = 0;
   out_1545513811425739451[95] = 1;
   out_1545513811425739451[96] = 0;
   out_1545513811425739451[97] = 0;
   out_1545513811425739451[98] = 0;
   out_1545513811425739451[99] = 0;
   out_1545513811425739451[100] = 0;
   out_1545513811425739451[101] = 0;
   out_1545513811425739451[102] = 0;
   out_1545513811425739451[103] = 0;
   out_1545513811425739451[104] = dt;
   out_1545513811425739451[105] = 0;
   out_1545513811425739451[106] = 0;
   out_1545513811425739451[107] = 0;
   out_1545513811425739451[108] = 0;
   out_1545513811425739451[109] = 0;
   out_1545513811425739451[110] = 0;
   out_1545513811425739451[111] = 0;
   out_1545513811425739451[112] = 0;
   out_1545513811425739451[113] = 0;
   out_1545513811425739451[114] = 1;
   out_1545513811425739451[115] = 0;
   out_1545513811425739451[116] = 0;
   out_1545513811425739451[117] = 0;
   out_1545513811425739451[118] = 0;
   out_1545513811425739451[119] = 0;
   out_1545513811425739451[120] = 0;
   out_1545513811425739451[121] = 0;
   out_1545513811425739451[122] = 0;
   out_1545513811425739451[123] = 0;
   out_1545513811425739451[124] = 0;
   out_1545513811425739451[125] = 0;
   out_1545513811425739451[126] = 0;
   out_1545513811425739451[127] = 0;
   out_1545513811425739451[128] = 0;
   out_1545513811425739451[129] = 0;
   out_1545513811425739451[130] = 0;
   out_1545513811425739451[131] = 0;
   out_1545513811425739451[132] = 0;
   out_1545513811425739451[133] = 1;
   out_1545513811425739451[134] = 0;
   out_1545513811425739451[135] = 0;
   out_1545513811425739451[136] = 0;
   out_1545513811425739451[137] = 0;
   out_1545513811425739451[138] = 0;
   out_1545513811425739451[139] = 0;
   out_1545513811425739451[140] = 0;
   out_1545513811425739451[141] = 0;
   out_1545513811425739451[142] = 0;
   out_1545513811425739451[143] = 0;
   out_1545513811425739451[144] = 0;
   out_1545513811425739451[145] = 0;
   out_1545513811425739451[146] = 0;
   out_1545513811425739451[147] = 0;
   out_1545513811425739451[148] = 0;
   out_1545513811425739451[149] = 0;
   out_1545513811425739451[150] = 0;
   out_1545513811425739451[151] = 0;
   out_1545513811425739451[152] = 1;
   out_1545513811425739451[153] = 0;
   out_1545513811425739451[154] = 0;
   out_1545513811425739451[155] = 0;
   out_1545513811425739451[156] = 0;
   out_1545513811425739451[157] = 0;
   out_1545513811425739451[158] = 0;
   out_1545513811425739451[159] = 0;
   out_1545513811425739451[160] = 0;
   out_1545513811425739451[161] = 0;
   out_1545513811425739451[162] = 0;
   out_1545513811425739451[163] = 0;
   out_1545513811425739451[164] = 0;
   out_1545513811425739451[165] = 0;
   out_1545513811425739451[166] = 0;
   out_1545513811425739451[167] = 0;
   out_1545513811425739451[168] = 0;
   out_1545513811425739451[169] = 0;
   out_1545513811425739451[170] = 0;
   out_1545513811425739451[171] = 1;
   out_1545513811425739451[172] = 0;
   out_1545513811425739451[173] = 0;
   out_1545513811425739451[174] = 0;
   out_1545513811425739451[175] = 0;
   out_1545513811425739451[176] = 0;
   out_1545513811425739451[177] = 0;
   out_1545513811425739451[178] = 0;
   out_1545513811425739451[179] = 0;
   out_1545513811425739451[180] = 0;
   out_1545513811425739451[181] = 0;
   out_1545513811425739451[182] = 0;
   out_1545513811425739451[183] = 0;
   out_1545513811425739451[184] = 0;
   out_1545513811425739451[185] = 0;
   out_1545513811425739451[186] = 0;
   out_1545513811425739451[187] = 0;
   out_1545513811425739451[188] = 0;
   out_1545513811425739451[189] = 0;
   out_1545513811425739451[190] = 1;
   out_1545513811425739451[191] = 0;
   out_1545513811425739451[192] = 0;
   out_1545513811425739451[193] = 0;
   out_1545513811425739451[194] = 0;
   out_1545513811425739451[195] = 0;
   out_1545513811425739451[196] = 0;
   out_1545513811425739451[197] = 0;
   out_1545513811425739451[198] = 0;
   out_1545513811425739451[199] = 0;
   out_1545513811425739451[200] = 0;
   out_1545513811425739451[201] = 0;
   out_1545513811425739451[202] = 0;
   out_1545513811425739451[203] = 0;
   out_1545513811425739451[204] = 0;
   out_1545513811425739451[205] = 0;
   out_1545513811425739451[206] = 0;
   out_1545513811425739451[207] = 0;
   out_1545513811425739451[208] = 0;
   out_1545513811425739451[209] = 1;
   out_1545513811425739451[210] = 0;
   out_1545513811425739451[211] = 0;
   out_1545513811425739451[212] = 0;
   out_1545513811425739451[213] = 0;
   out_1545513811425739451[214] = 0;
   out_1545513811425739451[215] = 0;
   out_1545513811425739451[216] = 0;
   out_1545513811425739451[217] = 0;
   out_1545513811425739451[218] = 0;
   out_1545513811425739451[219] = 0;
   out_1545513811425739451[220] = 0;
   out_1545513811425739451[221] = 0;
   out_1545513811425739451[222] = 0;
   out_1545513811425739451[223] = 0;
   out_1545513811425739451[224] = 0;
   out_1545513811425739451[225] = 0;
   out_1545513811425739451[226] = 0;
   out_1545513811425739451[227] = 0;
   out_1545513811425739451[228] = 1;
   out_1545513811425739451[229] = 0;
   out_1545513811425739451[230] = 0;
   out_1545513811425739451[231] = 0;
   out_1545513811425739451[232] = 0;
   out_1545513811425739451[233] = 0;
   out_1545513811425739451[234] = 0;
   out_1545513811425739451[235] = 0;
   out_1545513811425739451[236] = 0;
   out_1545513811425739451[237] = 0;
   out_1545513811425739451[238] = 0;
   out_1545513811425739451[239] = 0;
   out_1545513811425739451[240] = 0;
   out_1545513811425739451[241] = 0;
   out_1545513811425739451[242] = 0;
   out_1545513811425739451[243] = 0;
   out_1545513811425739451[244] = 0;
   out_1545513811425739451[245] = 0;
   out_1545513811425739451[246] = 0;
   out_1545513811425739451[247] = 1;
   out_1545513811425739451[248] = 0;
   out_1545513811425739451[249] = 0;
   out_1545513811425739451[250] = 0;
   out_1545513811425739451[251] = 0;
   out_1545513811425739451[252] = 0;
   out_1545513811425739451[253] = 0;
   out_1545513811425739451[254] = 0;
   out_1545513811425739451[255] = 0;
   out_1545513811425739451[256] = 0;
   out_1545513811425739451[257] = 0;
   out_1545513811425739451[258] = 0;
   out_1545513811425739451[259] = 0;
   out_1545513811425739451[260] = 0;
   out_1545513811425739451[261] = 0;
   out_1545513811425739451[262] = 0;
   out_1545513811425739451[263] = 0;
   out_1545513811425739451[264] = 0;
   out_1545513811425739451[265] = 0;
   out_1545513811425739451[266] = 1;
   out_1545513811425739451[267] = 0;
   out_1545513811425739451[268] = 0;
   out_1545513811425739451[269] = 0;
   out_1545513811425739451[270] = 0;
   out_1545513811425739451[271] = 0;
   out_1545513811425739451[272] = 0;
   out_1545513811425739451[273] = 0;
   out_1545513811425739451[274] = 0;
   out_1545513811425739451[275] = 0;
   out_1545513811425739451[276] = 0;
   out_1545513811425739451[277] = 0;
   out_1545513811425739451[278] = 0;
   out_1545513811425739451[279] = 0;
   out_1545513811425739451[280] = 0;
   out_1545513811425739451[281] = 0;
   out_1545513811425739451[282] = 0;
   out_1545513811425739451[283] = 0;
   out_1545513811425739451[284] = 0;
   out_1545513811425739451[285] = 1;
   out_1545513811425739451[286] = 0;
   out_1545513811425739451[287] = 0;
   out_1545513811425739451[288] = 0;
   out_1545513811425739451[289] = 0;
   out_1545513811425739451[290] = 0;
   out_1545513811425739451[291] = 0;
   out_1545513811425739451[292] = 0;
   out_1545513811425739451[293] = 0;
   out_1545513811425739451[294] = 0;
   out_1545513811425739451[295] = 0;
   out_1545513811425739451[296] = 0;
   out_1545513811425739451[297] = 0;
   out_1545513811425739451[298] = 0;
   out_1545513811425739451[299] = 0;
   out_1545513811425739451[300] = 0;
   out_1545513811425739451[301] = 0;
   out_1545513811425739451[302] = 0;
   out_1545513811425739451[303] = 0;
   out_1545513811425739451[304] = 1;
   out_1545513811425739451[305] = 0;
   out_1545513811425739451[306] = 0;
   out_1545513811425739451[307] = 0;
   out_1545513811425739451[308] = 0;
   out_1545513811425739451[309] = 0;
   out_1545513811425739451[310] = 0;
   out_1545513811425739451[311] = 0;
   out_1545513811425739451[312] = 0;
   out_1545513811425739451[313] = 0;
   out_1545513811425739451[314] = 0;
   out_1545513811425739451[315] = 0;
   out_1545513811425739451[316] = 0;
   out_1545513811425739451[317] = 0;
   out_1545513811425739451[318] = 0;
   out_1545513811425739451[319] = 0;
   out_1545513811425739451[320] = 0;
   out_1545513811425739451[321] = 0;
   out_1545513811425739451[322] = 0;
   out_1545513811425739451[323] = 1;
}
void h_4(double *state, double *unused, double *out_4720738351192482144) {
   out_4720738351192482144[0] = state[6] + state[9];
   out_4720738351192482144[1] = state[7] + state[10];
   out_4720738351192482144[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_1260804976980367599) {
   out_1260804976980367599[0] = 0;
   out_1260804976980367599[1] = 0;
   out_1260804976980367599[2] = 0;
   out_1260804976980367599[3] = 0;
   out_1260804976980367599[4] = 0;
   out_1260804976980367599[5] = 0;
   out_1260804976980367599[6] = 1;
   out_1260804976980367599[7] = 0;
   out_1260804976980367599[8] = 0;
   out_1260804976980367599[9] = 1;
   out_1260804976980367599[10] = 0;
   out_1260804976980367599[11] = 0;
   out_1260804976980367599[12] = 0;
   out_1260804976980367599[13] = 0;
   out_1260804976980367599[14] = 0;
   out_1260804976980367599[15] = 0;
   out_1260804976980367599[16] = 0;
   out_1260804976980367599[17] = 0;
   out_1260804976980367599[18] = 0;
   out_1260804976980367599[19] = 0;
   out_1260804976980367599[20] = 0;
   out_1260804976980367599[21] = 0;
   out_1260804976980367599[22] = 0;
   out_1260804976980367599[23] = 0;
   out_1260804976980367599[24] = 0;
   out_1260804976980367599[25] = 1;
   out_1260804976980367599[26] = 0;
   out_1260804976980367599[27] = 0;
   out_1260804976980367599[28] = 1;
   out_1260804976980367599[29] = 0;
   out_1260804976980367599[30] = 0;
   out_1260804976980367599[31] = 0;
   out_1260804976980367599[32] = 0;
   out_1260804976980367599[33] = 0;
   out_1260804976980367599[34] = 0;
   out_1260804976980367599[35] = 0;
   out_1260804976980367599[36] = 0;
   out_1260804976980367599[37] = 0;
   out_1260804976980367599[38] = 0;
   out_1260804976980367599[39] = 0;
   out_1260804976980367599[40] = 0;
   out_1260804976980367599[41] = 0;
   out_1260804976980367599[42] = 0;
   out_1260804976980367599[43] = 0;
   out_1260804976980367599[44] = 1;
   out_1260804976980367599[45] = 0;
   out_1260804976980367599[46] = 0;
   out_1260804976980367599[47] = 1;
   out_1260804976980367599[48] = 0;
   out_1260804976980367599[49] = 0;
   out_1260804976980367599[50] = 0;
   out_1260804976980367599[51] = 0;
   out_1260804976980367599[52] = 0;
   out_1260804976980367599[53] = 0;
}
void h_10(double *state, double *unused, double *out_3527418583963507715) {
   out_3527418583963507715[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_3527418583963507715[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_3527418583963507715[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_7685492195082252267) {
   out_7685492195082252267[0] = 0;
   out_7685492195082252267[1] = 9.8100000000000005*cos(state[1]);
   out_7685492195082252267[2] = 0;
   out_7685492195082252267[3] = 0;
   out_7685492195082252267[4] = -state[8];
   out_7685492195082252267[5] = state[7];
   out_7685492195082252267[6] = 0;
   out_7685492195082252267[7] = state[5];
   out_7685492195082252267[8] = -state[4];
   out_7685492195082252267[9] = 0;
   out_7685492195082252267[10] = 0;
   out_7685492195082252267[11] = 0;
   out_7685492195082252267[12] = 1;
   out_7685492195082252267[13] = 0;
   out_7685492195082252267[14] = 0;
   out_7685492195082252267[15] = 1;
   out_7685492195082252267[16] = 0;
   out_7685492195082252267[17] = 0;
   out_7685492195082252267[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_7685492195082252267[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_7685492195082252267[20] = 0;
   out_7685492195082252267[21] = state[8];
   out_7685492195082252267[22] = 0;
   out_7685492195082252267[23] = -state[6];
   out_7685492195082252267[24] = -state[5];
   out_7685492195082252267[25] = 0;
   out_7685492195082252267[26] = state[3];
   out_7685492195082252267[27] = 0;
   out_7685492195082252267[28] = 0;
   out_7685492195082252267[29] = 0;
   out_7685492195082252267[30] = 0;
   out_7685492195082252267[31] = 1;
   out_7685492195082252267[32] = 0;
   out_7685492195082252267[33] = 0;
   out_7685492195082252267[34] = 1;
   out_7685492195082252267[35] = 0;
   out_7685492195082252267[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_7685492195082252267[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_7685492195082252267[38] = 0;
   out_7685492195082252267[39] = -state[7];
   out_7685492195082252267[40] = state[6];
   out_7685492195082252267[41] = 0;
   out_7685492195082252267[42] = state[4];
   out_7685492195082252267[43] = -state[3];
   out_7685492195082252267[44] = 0;
   out_7685492195082252267[45] = 0;
   out_7685492195082252267[46] = 0;
   out_7685492195082252267[47] = 0;
   out_7685492195082252267[48] = 0;
   out_7685492195082252267[49] = 0;
   out_7685492195082252267[50] = 1;
   out_7685492195082252267[51] = 0;
   out_7685492195082252267[52] = 0;
   out_7685492195082252267[53] = 1;
}
void h_13(double *state, double *unused, double *out_7066220906659889628) {
   out_7066220906659889628[0] = state[3];
   out_7066220906659889628[1] = state[4];
   out_7066220906659889628[2] = state[5];
}
void H_13(double *state, double *unused, double *out_1951468848351965202) {
   out_1951468848351965202[0] = 0;
   out_1951468848351965202[1] = 0;
   out_1951468848351965202[2] = 0;
   out_1951468848351965202[3] = 1;
   out_1951468848351965202[4] = 0;
   out_1951468848351965202[5] = 0;
   out_1951468848351965202[6] = 0;
   out_1951468848351965202[7] = 0;
   out_1951468848351965202[8] = 0;
   out_1951468848351965202[9] = 0;
   out_1951468848351965202[10] = 0;
   out_1951468848351965202[11] = 0;
   out_1951468848351965202[12] = 0;
   out_1951468848351965202[13] = 0;
   out_1951468848351965202[14] = 0;
   out_1951468848351965202[15] = 0;
   out_1951468848351965202[16] = 0;
   out_1951468848351965202[17] = 0;
   out_1951468848351965202[18] = 0;
   out_1951468848351965202[19] = 0;
   out_1951468848351965202[20] = 0;
   out_1951468848351965202[21] = 0;
   out_1951468848351965202[22] = 1;
   out_1951468848351965202[23] = 0;
   out_1951468848351965202[24] = 0;
   out_1951468848351965202[25] = 0;
   out_1951468848351965202[26] = 0;
   out_1951468848351965202[27] = 0;
   out_1951468848351965202[28] = 0;
   out_1951468848351965202[29] = 0;
   out_1951468848351965202[30] = 0;
   out_1951468848351965202[31] = 0;
   out_1951468848351965202[32] = 0;
   out_1951468848351965202[33] = 0;
   out_1951468848351965202[34] = 0;
   out_1951468848351965202[35] = 0;
   out_1951468848351965202[36] = 0;
   out_1951468848351965202[37] = 0;
   out_1951468848351965202[38] = 0;
   out_1951468848351965202[39] = 0;
   out_1951468848351965202[40] = 0;
   out_1951468848351965202[41] = 1;
   out_1951468848351965202[42] = 0;
   out_1951468848351965202[43] = 0;
   out_1951468848351965202[44] = 0;
   out_1951468848351965202[45] = 0;
   out_1951468848351965202[46] = 0;
   out_1951468848351965202[47] = 0;
   out_1951468848351965202[48] = 0;
   out_1951468848351965202[49] = 0;
   out_1951468848351965202[50] = 0;
   out_1951468848351965202[51] = 0;
   out_1951468848351965202[52] = 0;
   out_1951468848351965202[53] = 0;
}
void h_14(double *state, double *unused, double *out_8413492641494458851) {
   out_8413492641494458851[0] = state[6];
   out_8413492641494458851[1] = state[7];
   out_8413492641494458851[2] = state[8];
}
void H_14(double *state, double *unused, double *out_2702435879359116930) {
   out_2702435879359116930[0] = 0;
   out_2702435879359116930[1] = 0;
   out_2702435879359116930[2] = 0;
   out_2702435879359116930[3] = 0;
   out_2702435879359116930[4] = 0;
   out_2702435879359116930[5] = 0;
   out_2702435879359116930[6] = 1;
   out_2702435879359116930[7] = 0;
   out_2702435879359116930[8] = 0;
   out_2702435879359116930[9] = 0;
   out_2702435879359116930[10] = 0;
   out_2702435879359116930[11] = 0;
   out_2702435879359116930[12] = 0;
   out_2702435879359116930[13] = 0;
   out_2702435879359116930[14] = 0;
   out_2702435879359116930[15] = 0;
   out_2702435879359116930[16] = 0;
   out_2702435879359116930[17] = 0;
   out_2702435879359116930[18] = 0;
   out_2702435879359116930[19] = 0;
   out_2702435879359116930[20] = 0;
   out_2702435879359116930[21] = 0;
   out_2702435879359116930[22] = 0;
   out_2702435879359116930[23] = 0;
   out_2702435879359116930[24] = 0;
   out_2702435879359116930[25] = 1;
   out_2702435879359116930[26] = 0;
   out_2702435879359116930[27] = 0;
   out_2702435879359116930[28] = 0;
   out_2702435879359116930[29] = 0;
   out_2702435879359116930[30] = 0;
   out_2702435879359116930[31] = 0;
   out_2702435879359116930[32] = 0;
   out_2702435879359116930[33] = 0;
   out_2702435879359116930[34] = 0;
   out_2702435879359116930[35] = 0;
   out_2702435879359116930[36] = 0;
   out_2702435879359116930[37] = 0;
   out_2702435879359116930[38] = 0;
   out_2702435879359116930[39] = 0;
   out_2702435879359116930[40] = 0;
   out_2702435879359116930[41] = 0;
   out_2702435879359116930[42] = 0;
   out_2702435879359116930[43] = 0;
   out_2702435879359116930[44] = 1;
   out_2702435879359116930[45] = 0;
   out_2702435879359116930[46] = 0;
   out_2702435879359116930[47] = 0;
   out_2702435879359116930[48] = 0;
   out_2702435879359116930[49] = 0;
   out_2702435879359116930[50] = 0;
   out_2702435879359116930[51] = 0;
   out_2702435879359116930[52] = 0;
   out_2702435879359116930[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_376508033301991984) {
  err_fun(nom_x, delta_x, out_376508033301991984);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_3293932473507733269) {
  inv_err_fun(nom_x, true_x, out_3293932473507733269);
}
void pose_H_mod_fun(double *state, double *out_8189327656671088405) {
  H_mod_fun(state, out_8189327656671088405);
}
void pose_f_fun(double *state, double dt, double *out_7345618461576849553) {
  f_fun(state,  dt, out_7345618461576849553);
}
void pose_F_fun(double *state, double dt, double *out_1545513811425739451) {
  F_fun(state,  dt, out_1545513811425739451);
}
void pose_h_4(double *state, double *unused, double *out_4720738351192482144) {
  h_4(state, unused, out_4720738351192482144);
}
void pose_H_4(double *state, double *unused, double *out_1260804976980367599) {
  H_4(state, unused, out_1260804976980367599);
}
void pose_h_10(double *state, double *unused, double *out_3527418583963507715) {
  h_10(state, unused, out_3527418583963507715);
}
void pose_H_10(double *state, double *unused, double *out_7685492195082252267) {
  H_10(state, unused, out_7685492195082252267);
}
void pose_h_13(double *state, double *unused, double *out_7066220906659889628) {
  h_13(state, unused, out_7066220906659889628);
}
void pose_H_13(double *state, double *unused, double *out_1951468848351965202) {
  H_13(state, unused, out_1951468848351965202);
}
void pose_h_14(double *state, double *unused, double *out_8413492641494458851) {
  h_14(state, unused, out_8413492641494458851);
}
void pose_H_14(double *state, double *unused, double *out_2702435879359116930) {
  H_14(state, unused, out_2702435879359116930);
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
