// SPDX-License-Identifier: GPL-2.0-only
#include "octa/StatusControl.hpp"

#include "octa/DeviceStatus.hpp"

#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <memory>
#include <string>
#include <string_view>

namespace octa {
namespace {

constexpr auto control_name = "OCTA-CAPTURE Status";
using Control = std::unique_ptr<snd_ctl_t, decltype(&snd_ctl_close)>;
using Info = std::unique_ptr<snd_ctl_elem_info_t, decltype(&snd_ctl_elem_info_free)>;
using Value = std::unique_ptr<snd_ctl_elem_value_t, decltype(&snd_ctl_elem_value_free)>;

std::expected<Info, StatusReadError> status_info(snd_ctl_t *control) {
  snd_ctl_elem_info_t *raw = nullptr;
  const int allocated = snd_ctl_elem_info_malloc(&raw);
  Info info(raw, &snd_ctl_elem_info_free);
  if (allocated < 0) {
    return std::unexpected(StatusReadError{.code = allocated, .operation = "allocate status info"});
  }
  snd_ctl_elem_info_set_interface(info.get(), SND_CTL_ELEM_IFACE_CARD);
  snd_ctl_elem_info_set_name(info.get(), control_name);
  const int queried = snd_ctl_elem_info(control, info.get());
  if (queried < 0) {
    return std::unexpected(StatusReadError{.code = queried, .operation = "status control info"});
  }
  if (snd_ctl_elem_info_get_type(info.get()) != SND_CTL_ELEM_TYPE_BYTES ||
      snd_ctl_elem_info_get_count(info.get()) != device_status_bytes ||
      snd_ctl_elem_info_is_readable(info.get()) == 0 ||
      snd_ctl_elem_info_is_writable(info.get()) != 0 ||
      snd_ctl_elem_info_is_volatile(info.get()) == 0) {
    return std::unexpected(
        StatusReadError{.code = -EPROTO, .operation = "status control metadata"});
  }
  return info;
}

}

std::expected<DeviceStatus, StatusReadError> read_device_status(std::string_view control_port) {
  constexpr std::size_t maximum_port_length = 256;
  if (!control_port.starts_with("hw:") || control_port.size() <= 3 ||
      control_port.size() > maximum_port_length ||
      control_port.find('\0') != std::string_view::npos) {
    return std::unexpected(StatusReadError{.code = -EINVAL, .operation = "hardware control port"});
  }
  const std::string name(control_port);
  snd_ctl_t *raw_control = nullptr;
  const int opened = snd_ctl_open(&raw_control, name.c_str(), SND_CTL_READONLY);
  const Control control(raw_control, &snd_ctl_close);
  if (opened < 0) {
    return std::unexpected(StatusReadError{.code = opened, .operation = "open status control"});
  }
  const auto info = status_info(control.get());
  if (!info) { return std::unexpected(info.error()); }
  snd_ctl_elem_value_t *raw_value = nullptr;
  const int allocated = snd_ctl_elem_value_malloc(&raw_value);
  const Value value(raw_value, &snd_ctl_elem_value_free);
  if (allocated < 0) {
    return std::unexpected(
        StatusReadError{.code = allocated, .operation = "allocate status value"});
  }
  snd_ctl_elem_value_set_interface(value.get(), SND_CTL_ELEM_IFACE_CARD);
  snd_ctl_elem_value_set_name(value.get(), control_name);
  const int received = snd_ctl_elem_read(control.get(), value.get());
  if (received < 0) {
    return std::unexpected(StatusReadError{.code = received, .operation = "read status control"});
  }
  std::array<std::uint8_t, device_status_bytes> bytes{};
  for (std::size_t index = 0; index < bytes.size(); ++index) {
    bytes[index] = snd_ctl_elem_value_get_byte(value.get(), static_cast<unsigned int>(index));
  }
  const auto decoded = decode_device_status(bytes);
  if (!decoded) {
    return std::unexpected(StatusReadError{.code = -EBADMSG, .operation = "status packet"});
  }
  return *decoded;
}

}
