// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PcmMeterControl.hpp"

#include "octa/PcmMeters.hpp"
#include "octa/StatusControl.hpp"

#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <memory>
#include <string>
#include <string_view>
#include <utility>

namespace octa {
namespace {

constexpr auto peaks_name = "OCTA-CAPTURE PCM Peaks";
constexpr auto switch_name = "OCTA-CAPTURE PCM Meter Switch";
using Control = std::unique_ptr<snd_ctl_t, decltype(&snd_ctl_close)>;
using Info = std::unique_ptr<snd_ctl_elem_info_t, decltype(&snd_ctl_elem_info_free)>;
using Value = std::unique_ptr<snd_ctl_elem_value_t, decltype(&snd_ctl_elem_value_free)>;

std::expected<void, StatusReadError> check_info(snd_ctl_t *control, bool meter_switch) {
  snd_ctl_elem_info_t *raw = nullptr;
  const int allocated = snd_ctl_elem_info_malloc(&raw);
  const Info info(raw, &snd_ctl_elem_info_free);
  if (allocated < 0) {
    return std::unexpected(
        StatusReadError{.code = allocated, .operation = "allocate PCM meter info"});
  }
  snd_ctl_elem_info_set_interface(info.get(), SND_CTL_ELEM_IFACE_CARD);
  snd_ctl_elem_info_set_name(info.get(), meter_switch ? switch_name : peaks_name);
  const int queried = snd_ctl_elem_info(control, info.get());
  if (queried < 0) {
    return std::unexpected(StatusReadError{.code = queried, .operation = "PCM meter info"});
  }
  const auto type = meter_switch ? SND_CTL_ELEM_TYPE_BOOLEAN : SND_CTL_ELEM_TYPE_BYTES;
  const auto count = meter_switch ? 1 : pcm_meter_bytes;
  if (snd_ctl_elem_info_get_type(info.get()) != type ||
      snd_ctl_elem_info_get_count(info.get()) != count ||
      snd_ctl_elem_info_is_readable(info.get()) == 0 ||
      (snd_ctl_elem_info_is_writable(info.get()) != 0) != meter_switch ||
      snd_ctl_elem_info_is_volatile(info.get()) == 0) {
    return std::unexpected(StatusReadError{.code = -EPROTO, .operation = "PCM meter metadata"});
  }
  return {};
}

std::expected<Value, StatusReadError> make_value(const char *name) {
  snd_ctl_elem_value_t *raw = nullptr;
  const int allocated = snd_ctl_elem_value_malloc(&raw);
  Value value(raw, &snd_ctl_elem_value_free);
  if (allocated < 0) {
    return std::unexpected(
        StatusReadError{.code = allocated, .operation = "allocate PCM meter value"});
  }
  snd_ctl_elem_value_set_interface(value.get(), SND_CTL_ELEM_IFACE_CARD);
  snd_ctl_elem_value_set_name(value.get(), name);
  return value;
}

}

struct PcmMeterControl::Impl {
  Control control{nullptr, &snd_ctl_close};
  bool enabled = false;

  ~Impl() {
    if (enabled) { [[maybe_unused]] const auto stopped = enable(false); }
  }

  [[nodiscard]] std::expected<void, StatusReadError> enable(bool requested) {
    auto value = make_value(switch_name);
    if (!value) { return std::unexpected(value.error()); }
    snd_ctl_elem_value_set_integer(value->get(), 0, requested ? 1 : 0);
    // Even a failed write can have reached hardware: destruction retries stop.
    enabled = enabled || requested;
    const int written = snd_ctl_elem_write(control.get(), value->get());
    if (written < 0) {
      return std::unexpected(StatusReadError{.code = written, .operation = "PCM meter switch"});
    }
    enabled = requested;
    return {};
  }
};

PcmMeterControl::PcmMeterControl(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}

PcmMeterControl::~PcmMeterControl() = default;
PcmMeterControl::PcmMeterControl(PcmMeterControl &&) noexcept = default;
PcmMeterControl &PcmMeterControl::operator=(PcmMeterControl &&) noexcept = default;

std::expected<PcmMeterControl, StatusReadError> PcmMeterControl::open(std::string_view port) {
  constexpr std::size_t maximum_port_length = 256;
  if (!port.starts_with("hw:") || port.size() <= 3 || port.size() > maximum_port_length ||
      port.find('\0') != std::string_view::npos) {
    return std::unexpected(
        StatusReadError{.code = -EINVAL, .operation = "hardware PCM meter port"});
  }
  auto impl = std::make_unique<Impl>();
  const std::string name(port);
  snd_ctl_t *raw = nullptr;
  const int opened = snd_ctl_open(&raw, name.c_str(), 0);
  impl->control.reset(raw);
  if (opened < 0) {
    return std::unexpected(StatusReadError{.code = opened, .operation = "open PCM meter control"});
  }
  for (const bool meter_switch : {false, true}) {
    const auto checked = check_info(impl->control.get(), meter_switch);
    if (!checked) { return std::unexpected(checked.error()); }
  }
  return PcmMeterControl(std::move(impl));
}

std::expected<void, StatusReadError> PcmMeterControl::enable(bool enabled) {
  if (!impl_) {
    return std::unexpected(
        StatusReadError{.code = -EBADF, .operation = "closed PCM meter control"});
  }
  return impl_->enable(enabled);
}

std::expected<PcmMeters, StatusReadError> PcmMeterControl::read() {
  if (!impl_) {
    return std::unexpected(
        StatusReadError{.code = -EBADF, .operation = "closed PCM meter control"});
  }
  auto value = make_value(peaks_name);
  if (!value) { return std::unexpected(value.error()); }
  const int received = snd_ctl_elem_read(impl_->control.get(), value->get());
  if (received < 0) {
    return std::unexpected(
        StatusReadError{.code = received, .operation = "read PCM meter control"});
  }
  std::array<std::uint8_t, pcm_meter_bytes> bytes{};
  for (std::size_t index = 0; index < bytes.size(); ++index) {
    bytes[index] = snd_ctl_elem_value_get_byte(value->get(), static_cast<unsigned int>(index));
  }
  const auto decoded = decode_pcm_meters(bytes);
  if (!decoded) {
    return std::unexpected(StatusReadError{.code = -EBADMSG, .operation = "PCM meter packet"});
  }
  return *decoded;
}

}
