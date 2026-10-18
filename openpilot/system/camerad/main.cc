#include "system/camerad/cameras/camera_common.h"

#include <cassert>

#include "common/hardware/hw.h"
#include "common/params.h"
#include "common/util.h"

int main(int argc, char *argv[]) {
  // isolcpus 不再让 camerad 独占 core 6：bigmodeld 的 wide 取帧与两路 dequeue（FIFO 50）也绑在这里，
  // camerad 若是普通调度，每帧会被它们抢 ~0.8 ms，路摄出帧随之推迟（Chipmunk OS 94 号实测）。
  // FIFO 52：高于 bigmodeld 的 50，低于 pandad / modeld 的 54
  int ret;
  if (!Hardware::PC()) {
    ret = util::set_realtime_priority(52);
    assert(ret == 0);
  }
  ret = util::set_core_affinity({6});
  assert(ret == 0 || Params().getBool("IsOffroad")); // failure ok while offroad due to offlining cores

  camerad_thread();
  return 0;
}
