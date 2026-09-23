"""回归测试:is_run_model 属性(a3e5fdf10 引入的缺失)。

2026-09-23 实车事故:砍模型下载器(a3e5fdf10)删了
``self.is_run_model = 'run_model' in jits`` 赋值,但 run() 里留下引用——
modeld 拿到 CarParams 后第一帧必 AttributeError,3 秒崩溃循环,校准卡 0%。
三层掩护让它漏网:冒烟的假线束不写 params CarParams(modeld 阻塞在
CP 等待,推理路径从未被冒烟覆盖)、process.py 把子进程 stderr 扔
/dev/null(traceback 丢失)、上一版 flat release 同样带病(首次上路才暴露)。

ModelState 需要 QCOM GPU 无法在 CI 构造,这里做源码级断言(与
test_params_gate 检查 params_keys.h 同款手法)。
"""
import inspect

from openpilot.selfdrive.modeld import modeld


def test_modelstate_init_sets_is_run_model():
  init_src = inspect.getsource(modeld.ModelState.__init__)
  assert "self.is_run_model" in init_src, \
    "ModelState.__init__ must set self.is_run_model (a3e5fdf10 dropped it; run() references it at line 179)"


def test_run_method_references_is_run_model():
  """引用端不变的情况下,赋值端必须在 __init__ 里(两处一起改的话本测试会提醒更新)。"""
  run_src = inspect.getsource(modeld.ModelState.run)
  assert "self.is_run_model" in run_src
