"""Duck-type Params that records writes, shared by the lanlinkd API tests."""

class FakeParams:
  """记录写操作的 duck-type Params。"""

  def __init__(self, data=None):
    self.data = dict(data or {})
    self.puts = []
    self.removes = []

  def get(self, key):
    return self.data.get(key)

  def get_bool(self, key):
    v = self.data.get(key)
    return v in (True, "1", 1)

  def put(self, key, value, block=False):
    self.puts.append((key, value))
    self.data[key] = value

  def put_bool(self, key, value, block=False):
    self.puts.append((key, value))
    self.data[key] = value

  def remove(self, key):
    self.removes.append(key)
    self.data.pop(key, None)
