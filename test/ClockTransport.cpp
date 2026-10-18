// SPDX-License-Identifier: GPL-2.0-only
#include "ClockTransport.hpp"

#include "ClockTypes.h"
#include "helper.h"
#include "linux/delay.h"
#include "linux/usb.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <span>

namespace {
octa::clock_test::Transport context;
constexpr unsigned int clock_request = 3;
constexpr unsigned int status_selector = 1;
constexpr unsigned int ready_selector = 2;
constexpr unsigned int write_selector = 8;
constexpr unsigned int poll_delay = 200;
constexpr unsigned int byte_shift = 8;
constexpr unsigned int rate_high_shift = byte_shift * 2;
constexpr std::uint8_t keep_nonbusy_flags = 0xfe;

int read_reply(u16 selector, std::span<std::uint8_t> data, int result) {
  std::array<std::uint8_t, 4> reply{};
  if (selector == status_selector && data.size() == reply.size()) {
    reply = {static_cast<std::uint8_t>(context.rate),
             static_cast<std::uint8_t>(context.rate >> byte_shift),
             static_cast<std::uint8_t>(context.rate >> rate_high_shift),
             context.flags};
    if (context.writes != 0 && context.busy_queries++ < context.busy_replies) { reply[3] |= 1; }
  } else if (selector == ready_selector && data.size() == 1) {
    reply[0] = context.ready_queries++ >= context.ready_delay ? context.ready_value : 0;
  } else {
    context.invalid_request = true;
    return -EPROTO;
  }
  if (result >= 0) {
    const auto count = std::min(data.size(), static_cast<std::size_t>(result));
    std::ranges::copy(std::span(reply).first(count), data.begin());
  }
  return result;
}

int write_reply(u16 selector, std::span<const std::uint8_t> data, int result) {
  if (selector != write_selector || data.size() != context.frame.size()) {
    context.invalid_request = true;
    return -EPROTO;
  }
  ++context.writes;
  std::ranges::copy(data, context.frame.begin());
  if (result == static_cast<int>(data.size())) {
    if (!context.wrong_rate) {
      context.rate = static_cast<unsigned int>(data[1]) |
                     (static_cast<unsigned int>(data[2]) << byte_shift) |
                     (static_cast<unsigned int>(data[3]) << rate_high_shift);
    }
    context.flags &= keep_nonbusy_flags;
    if (context.invalid_after_write) { context.flags |= 2; }
  }
  return result;
}
}

namespace octa::clock_test {
Transport &transport() {
  return context;
}

void reset(unsigned int rate) {
  context = Transport{};
  context.chip.dev = &context.device;
  context.rate = rate;
}
}

extern "C" unsigned int usb_rcvctrlpipe(usb_device *device, unsigned int endpoint) {
  if (device != &context.device || endpoint != 0) { context.invalid_request = true; }
  return USB_DIR_IN;
}

extern "C" unsigned int usb_sndctrlpipe(usb_device *device, unsigned int endpoint) {
  if (device != &context.device || endpoint != 0) { context.invalid_request = true; }
  return USB_DIR_OUT;
}

extern "C" void msleep(unsigned int milliseconds) {
  ++context.sleeps;
  if (milliseconds != poll_delay) { context.invalid_request = true; }
}

extern "C" int octa_test_ctl_msg(const octa_test_request *request) {
  ++context.calls;
  const bool incoming = (request->request_type & USB_DIR_IN) != 0;
  if (request->device != &context.device || request->request != clock_request ||
      request->index != 0 ||
      request->request_type != (USB_TYPE_VENDOR | (incoming ? USB_DIR_IN : USB_DIR_OUT)) ||
      request->pipe != (incoming ? USB_DIR_IN : USB_DIR_OUT)) {
    context.invalid_request = true;
    return -EPROTO;
  }
  const int result = context.calls == context.fault_call ? context.fault_result : request->size;
  const std::span bytes(static_cast<std::uint8_t *>(request->data), request->size);
  return incoming ? read_reply(request->value, bytes, result)
                  : write_reply(request->value, bytes, result);
}
