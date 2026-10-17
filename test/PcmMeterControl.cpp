// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PcmMeterControl.hpp"

#include "PcmMeterPacket.hpp"
#include "octa/PcmMeters.hpp"

#include <alsa/asoundlib.h>
#include <cerrno>
#include <cstddef>
#include <iostream>
#include <string_view>
#include <utility>

namespace {

constexpr unsigned int sample_rate = 48000;

struct FakeControl {
  fixture::Packet bytes = fixture::packet(sample_rate);
  snd_ctl_elem_type_t type = SND_CTL_ELEM_TYPE_BYTES;
  unsigned int count = octa::pcm_meter_bytes;
  bool selecting_switch = false;
  unsigned int writes = 0;
  int write_error = 0;
  int last_switch = -1;
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
  {
    auto control = octa::PcmMeterControl::open("hw:fake");
    if (!require(control.has_value(), "validated persistent control opens")) { return false; }
    const auto enabled = control->enable(true);
    const auto meters = control->read();
    if (!require(enabled && meters && meters->capture.rate == sample_rate && io.opens == 1 &&
                     io.closes == 0 && io.mode == 0 && io.last_switch == 1 && io.reads == 1,
                 "fixed control enable and bounded typed read")) {
      return false;
    }
  }
  return require(io.closes == 1 && io.writes == 2 && io.last_switch == 0,
                 "destruction disables before closing");
}

bool failures() {
  io = {};
  auto invalid = octa::PcmMeterControl::open("default");
  if (!require(!invalid && io.opens == 0, "reject ALSA plugins before open")) { return false; }
  io.info_error = -ENOENT;
  auto missing = octa::PcmMeterControl::open("hw:fake");
  if (!require(!missing && io.closes == 1 && io.writes == 0, "missing control never writes")) {
    return false;
  }
  io = {};
  io.count = 1;
  auto malformed = octa::PcmMeterControl::open("hw:fake");
  if (!require(!malformed && io.reads == 0 && io.writes == 0, "reject wrong metadata")) {
    return false;
  }
  io = {};
  auto control = octa::PcmMeterControl::open("hw:fake");
  if (!require(control.has_value(), "open before fault injection")) { return false; }
  io.bytes[0] = 0;
  const auto packet = control->read();
  if (!require(!packet && packet.error().code == -EBADMSG, "reject malformed wire packet")) {
    return false;
  }
  io.read_error = -ENODEV;
  const auto removed = control->read();
  return require(!removed && removed.error().code == -ENODEV, "disconnect propagates");
}

bool move_cleanup() {
  io = {};
  {
    auto first = octa::PcmMeterControl::open("hw:fake");
    auto second = octa::PcmMeterControl::open("hw:fake");
    if (!first || !second || !first->enable(true) || !second->enable(true)) { return false; }
    *first = std::move(*second);
    if (!require(io.closes == 1 && io.writes == 3 && io.last_switch == 0,
                 "move assignment releases its previous enabled owner")) {
      return false;
    }
  }
  return require(io.closes == 2 && io.writes == 4 && io.last_switch == 0,
                 "moved-from destruction does not double close");
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
  const std::string_view name(snd_ctl_elem_info_get_name(info));
  io.selecting_switch = name == "OCTA-CAPTURE PCM Meter Switch";
  if (snd_ctl_elem_info_get_interface(info) != SND_CTL_ELEM_IFACE_CARD ||
      (!io.selecting_switch && name != "OCTA-CAPTURE PCM Peaks")) {
    return -EINVAL;
  }
  return io.info_error;
}

extern "C" snd_ctl_elem_type_t snd_ctl_elem_info_get_type(const snd_ctl_elem_info_t * /*info*/) {
  return io.selecting_switch ? SND_CTL_ELEM_TYPE_BOOLEAN : io.type;
}

extern "C" unsigned int snd_ctl_elem_info_get_count(const snd_ctl_elem_info_t * /*info*/) {
  return io.selecting_switch ? 1 : io.count;
}

extern "C" int snd_ctl_elem_info_is_readable(const snd_ctl_elem_info_t * /*info*/) {
  return io.readable;
}

extern "C" int snd_ctl_elem_info_is_writable(const snd_ctl_elem_info_t * /*info*/) {
  return io.selecting_switch ? 1 : io.writable;
}

extern "C" int snd_ctl_elem_info_is_volatile(const snd_ctl_elem_info_t * /*info*/) {
  return io.is_volatile;
}

extern "C" int snd_ctl_elem_read(snd_ctl_t * /*control*/, snd_ctl_elem_value_t *data) {
  ++io.reads;
  if (snd_ctl_elem_value_get_interface(data) != SND_CTL_ELEM_IFACE_CARD ||
      std::string_view(snd_ctl_elem_value_get_name(data)) != "OCTA-CAPTURE PCM Peaks") {
    return -EINVAL;
  }
  if (io.read_error != 0) { return io.read_error; }
  for (std::size_t index = 0; index < io.bytes.size(); ++index) {
    snd_ctl_elem_value_set_byte(data, static_cast<unsigned int>(index), io.bytes[index]);
  }
  return 0;
}

extern "C" int snd_ctl_elem_write(snd_ctl_t * /*control*/, snd_ctl_elem_value_t *data) {
  if (snd_ctl_elem_value_get_interface(data) != SND_CTL_ELEM_IFACE_CARD ||
      std::string_view(snd_ctl_elem_value_get_name(data)) != "OCTA-CAPTURE PCM Meter Switch") {
    return -EINVAL;
  }
  ++io.writes;
  io.last_switch = static_cast<int>(snd_ctl_elem_value_get_integer(data, 0));
  return io.write_error;
}

int main() {
  return successful_read() && failures() && move_cleanup() ? 0 : 1;
}
