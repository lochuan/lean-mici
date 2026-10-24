#pragma once

// 下行（手机 → 车机）定长消息分帧：HELLO 48 / REPLY 8480 / ERR 40 B。
// 只做字节拼接 + 按 MsgHdr.type/len 定整条长度；REPLY 被 TCP 拆成多次 read_some（切点可能在
// NavExt 中间）时按累计字节数等齐，凑满才交出整条。纯逻辑、无 socket 依赖，宿主单测直接编译。
//
// 用法：append(read_some 的字节) → 循环 next()；kMessage 时 data()/size() 即整条消息，
// 处理完 consume(size)。kBadHeader = 首部 type/len 不在协议内（含旧 v2 的 8280 B REPLY），
// magic/版本先于 type/len 校验；kBadVersion 由前 16 B 返回，调用方保持错版状态并断连。

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "frame_codec.h"

class MsgFramer {
 public:
  enum class Result { kNeedMore, kMessage, kBadHeader, kBadVersion };
  struct Message {
    uint8_t type = 0;
    size_t size = 0;  // 整条线上字节数
  };

  void append(const uint8_t* data, size_t len) { buf_.insert(buf_.end(), data, data + len); }

  Result next(Message* out) const {
    if (buf_.size() < bgm1::kMsgHdrSize) return Result::kNeedMore;
    if (bgm1::read_u32_le(buf_.data()) != bgm1::kMagic) return Result::kBadHeader;
    if (buf_[4] != bgm1::kVersion) return Result::kBadVersion;
    const uint8_t type = buf_[5];
    const uint32_t len = bgm1::read_u32_le(buf_.data() + 12);
    size_t need = 0;
    if (type == bgm1::kTypeHello && len == bgm1::kHelloPayloadSize) {
      need = bgm1::kHelloWireSize;
    } else if (type == bgm1::kTypeReply && len == bgm1::kReplyPayloadSize) {
      need = bgm1::kReplyWireSize;
    } else if (type == bgm1::kTypeErr && len == bgm1::kErrPayloadSize) {
      need = bgm1::kErrWireSize;
    } else {
      return Result::kBadHeader;
    }
    if (buf_.size() < need) return Result::kNeedMore;
    out->type = type;
    out->size = need;
    return Result::kMessage;
  }

  // kMessage 时指向整条消息起点；下一次 append 后指针可能失效，处理完立即用。
  const uint8_t* data() const { return buf_.data(); }
  size_t size() const { return buf_.size(); }
  uint8_t peer_version() const { return buf_.size() >= bgm1::kMsgHdrSize ? buf_[4] : 0; }

  void consume(size_t n) { buf_.erase(buf_.begin(), buf_.begin() + std::min(n, buf_.size())); }
  void clear() { buf_.clear(); }

 private:
  std::vector<uint8_t> buf_;
};
