// SPDX-License-Identifier: GPL-2.0-only
#include "octa/StatusControl.hpp"

#include "octa/DeviceStatus.hpp"

#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <string>
#include <string_view>

namespace {

struct FakeControl {
  std::array<std::uint8_t, 8> bytes = {1, 0, 0, 0x80, 0xbb, 0, 0, 1};
  snd_ctl_elem_type_t type = SND_CTL_ELEM_TYPE_BYTES;
  unsigned int count = 8;
  int readable = 1;
  int writable = 0;
  int is_volatile = 1;
  int open_error = 0;
  int info_error = 0;
  int read_error = 0;
  int mode = 0;
  unsigned int opens = 0;
  unsigned int closes = 0;
  unsigned int reads = 0;
};

FakeControl io;
std::max_align_t token{};

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool successful_read() {
  io = {};
  const auto status = octa::read_device_status("hw:fake");
  constexpr std::uint32_t expected_rate = 48000;
  return require(status && status->raw == io.bytes && status->sample_rate_hz == expected_rate &&
                     io.mode == SND_CTL_READONLY && io.opens == 1 && io.reads == 1 &&
                     io.closes == 1,
                 "typed read opens read-only, addresses CARD status and closes its handle");
}

bool invalid_ports() {
  io = {};
  constexpr std::array<std::string_view, 4> names = {"", "hw:", "default", "plughw:0"};
  for (const auto name : names) {
    const auto status = octa::read_device_status(name);
    if (!require(!status && status.error().code == -EINVAL && io.opens == 0,
                 "invalid hardware port rejected before ALSA")) {
      return false;
    }
  }
  const std::string long_name = "hw:" + std::string(256, 'x');
  constexpr auto embedded = std::to_array("hw:fake\0suffix");
  const auto large = octa::read_device_status(long_name);
  const auto nul = octa::read_device_status(std::string_view(embedded.data(), embedded.size() - 1));
  return require(!large && !nul && io.opens == 0,
                 "oversized and NUL-containing control names rejected");
}

bool retained_handle() {
  io = {};
  auto *const control = reinterpret_cast<snd_ctl_t *>(&token);
  const auto first = octa::read_device_status_handle(control);
  const auto second = octa::read_device_status_handle(control);
  io.read_error = -ENODEV;
  const auto removed = octa::read_device_status_handle(control);
  const auto null = octa::read_device_status_handle(nullptr);
  return require(
      first && second && !removed && removed.error().code == -ENODEV && !null &&
          null.error().code == -EINVAL && io.opens == 0 && io.closes == 0 && io.reads == 3,
      "retained hardware handle reads preserve ownership and cannot reopen a reused card number");
}

bool invalid_metadata() {
  constexpr unsigned int cases = 6;
  for (unsigned int index = 0; index < cases; ++index) {
    io = {};
    if (index == 0) { io.type = SND_CTL_ELEM_TYPE_INTEGER; }
    if (index == 1) { io.count = 7; }
    if (index == 2) { io.count = 9; }
    if (index == 3) { io.readable = 0; }
    if (index == 4) { io.writable = 1; }
    if (index == 5) { io.is_volatile = 0; }
    const auto status = octa::read_device_status("hw:fake");
    if (!require(!status && status.error().code == -EPROTO && io.reads == 0 && io.closes == 1,
                 "wrong type/size/access/volatility rejected before reading bytes")) {
      return false;
    }
  }
  return true;
}

bool transport_failures() {
  io = {};
  io.open_error = -EACCES;
  const auto open = octa::read_device_status("hw:fake");
  if (!require(!open && open.error().code == -EACCES && io.closes == 0,
               "open failure preserves error")) {
    return false;
  }
  io = {};
  io.info_error = -ENOENT;
  const auto missing = octa::read_device_status("hw:fake");
  if (!require(!missing && missing.error().code == -ENOENT && io.reads == 0 && io.closes == 1,
               "missing kernel control closes its handle")) {
    return false;
  }
  io = {};
  io.read_error = -ENODEV;
  const auto removed = octa::read_device_status("hw:fake");
  if (!require(!removed && removed.error().code == -ENODEV && io.closes == 1,
               "read/disconnect error closes its handle")) {
    return false;
  }
  io = {};
  io.bytes[0] = 0;
  const auto malformed = octa::read_device_status("hw:fake");
  return require(!malformed && malformed.error().code == -EBADMSG && io.closes == 1,
                 "malformed kernel status rejected after bounded read");
}

}

extern "C" int snd_ctl_open(snd_ctl_t **ctl, const char * /*name*/, int mode) {
  io.mode = mode;
  if (io.open_error != 0) { return io.open_error; }
  ++io.opens;
  *ctl = reinterpret_cast<snd_ctl_t *>(&token);
  return 0;
}

extern "C" int snd_ctl_close(snd_ctl_t * /*control*/) {
  ++io.closes;
  return 0;
}

extern "C" int snd_ctl_elem_info(snd_ctl_t * /*control*/, snd_ctl_elem_info_t *info) {
  if (snd_ctl_elem_info_get_interface(info) != SND_CTL_ELEM_IFACE_CARD ||
      std::string_view(snd_ctl_elem_info_get_name(info)) != "OCTA-CAPTURE Status") {
    return -EINVAL;
  }
  return io.info_error;
}

extern "C" snd_ctl_elem_type_t snd_ctl_elem_info_get_type(const snd_ctl_elem_info_t * /*info*/) {
  return io.type;
}

extern "C" unsigned int snd_ctl_elem_info_get_count(const snd_ctl_elem_info_t * /*info*/) {
  return io.count;
}

extern "C" int snd_ctl_elem_info_is_readable(const snd_ctl_elem_info_t * /*info*/) {
  return io.readable;
}

extern "C" int snd_ctl_elem_info_is_writable(const snd_ctl_elem_info_t * /*info*/) {
  return io.writable;
}

extern "C" int snd_ctl_elem_info_is_volatile(const snd_ctl_elem_info_t * /*info*/) {
  return io.is_volatile;
}

extern "C" int snd_ctl_elem_read(snd_ctl_t * /*control*/, snd_ctl_elem_value_t *data) {
  ++io.reads;
  if (snd_ctl_elem_value_get_interface(data) != SND_CTL_ELEM_IFACE_CARD ||
      std::string_view(snd_ctl_elem_value_get_name(data)) != "OCTA-CAPTURE Status") {
    return -EINVAL;
  }
  if (io.read_error != 0) { return io.read_error; }
  for (std::size_t index = 0; index < io.bytes.size(); ++index) {
    snd_ctl_elem_value_set_byte(data, static_cast<unsigned int>(index), io.bytes[index]);
  }
  return 0;
}

int main() {
  return successful_read() && invalid_ports() && invalid_metadata() && transport_failures() &&
                 retained_handle()
             ? 0
             : 1;
}
