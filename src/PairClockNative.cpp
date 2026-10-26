// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PairClockNative.hpp"

#include "octa/DeviceStatus.hpp"
#include "octa/PairClock.hpp"
#include "octa/PairPcm.hpp"
#include "octa/StatusControl.hpp"

#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <expected>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>

namespace octa {
namespace {
using Reply = std::expected<void, StatusReadError>;
constexpr int maximum_card_index = 255;
constexpr std::size_t maximum_serial_length = 64;
constexpr std::array<std::uint32_t, 3> rates = {44100, 48000, 96000};

bool valid_serial(std::string_view serial) {
  return !serial.empty() && serial.size() <= maximum_serial_length &&
         std::ranges::all_of(serial, [](char value) {
           return (value >= 'a' && value <= 'z') || (value >= 'A' && value <= 'Z') ||
                  (value >= '0' && value <= '9') || value == '_' || value == '-';
         });
}

std::expected<std::string, StatusReadError> text(const std::filesystem::path &path) {
  const std::unique_ptr<std::FILE, decltype(&std::fclose)> file(std::fopen(path.c_str(), "re"),
                                                                &std::fclose);
  if (!file) {
    return std::unexpected(
        StatusReadError{.code = -errno, .operation = "open paired USB identity"});
  }
  // sysfs sizes are not a reliable bound. Read one byte beyond serial + newline.
  std::array<char, maximum_serial_length + 2> buffer{};
  const auto count = std::fread(buffer.data(), 1, buffer.size(), file.get());
  if (std::ferror(file.get()) != 0) {
    return std::unexpected(StatusReadError{.code = -EIO, .operation = "read paired USB identity"});
  }
  std::string result(buffer.data(), count);
  if (result.ends_with('\n')) { result.pop_back(); }
  if (result.empty() || result.size() > maximum_serial_length) {
    return std::unexpected(
        StatusReadError{.code = -EPROTO, .operation = "paired USB identity size"});
  }
  return result;
}

Reply release_parameters(snd_pcm_t *pcm) {
  const auto state = snd_pcm_state(pcm);
  if (state == SND_PCM_STATE_DISCONNECTED) {
    return std::unexpected(
        StatusReadError{.code = -ENODEV, .operation = "paired EXP PCM disconnected"});
  }
  if (state == SND_PCM_STATE_OPEN) { return {}; }
  if (state != SND_PCM_STATE_SETUP) {
    const int dropped = snd_pcm_drop(pcm);
    if (dropped < 0) {
      return std::unexpected(
          StatusReadError{.code = dropped, .operation = "drop paired EXP capture"});
    }
  }
  const int freed = snd_pcm_hw_free(pcm);
  if (freed < 0) {
    return std::unexpected(
        StatusReadError{.code = freed, .operation = "free paired EXP capture parameters"});
  }
  return {};
}

int constrain(snd_pcm_t *pcm, snd_pcm_hw_params_t *parameters, std::uint32_t rate) {
  constexpr unsigned int capture_channels = 12;
  constexpr snd_pcm_uframes_t period = 1024;
  constexpr snd_pcm_uframes_t buffer = 8192;
  int result = snd_pcm_hw_params_any(pcm, parameters);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_access(pcm, parameters, SND_PCM_ACCESS_RW_INTERLEAVED);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_format(pcm, parameters, SND_PCM_FORMAT_S24_3LE);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_channels(pcm, parameters, capture_channels);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_rate(pcm, parameters, rate, 0);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_period_size(pcm, parameters, period, 0);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_buffer_size(pcm, parameters, buffer);
  if (result < 0) { return result; }
  return snd_pcm_hw_params(pcm, parameters);
}

Reply prepare(snd_pcm_t *pcm, std::uint32_t rate) {
  const auto released = release_parameters(pcm);
  if (!released) { return released; }
  snd_pcm_hw_params_t *raw = nullptr;
  const int allocated = snd_pcm_hw_params_malloc(&raw);
  const std::unique_ptr<snd_pcm_hw_params_t, decltype(&snd_pcm_hw_params_free)> parameters(
      raw, &snd_pcm_hw_params_free);
  if (allocated < 0 || !parameters) {
    return std::unexpected(StatusReadError{.code = allocated < 0 ? allocated : -EIO,
                                           .operation = "allocate paired EXP capture parameters"});
  }
  const int configured = constrain(pcm, parameters.get(), rate);
  if (configured < 0) {
    return std::unexpected(
        StatusReadError{.code = configured, .operation = "configure paired EXP capture"});
  }
  const int prepared = snd_pcm_prepare(pcm);
  if (prepared < 0) {
    return std::unexpected(
        StatusReadError{.code = prepared, .operation = "prepare paired EXP capture"});
  }
  return {};
}

class NativeClock final : public PairClockIo {
  struct CloseControl {
    void operator()(snd_ctl_t *control) const noexcept { snd_ctl_close(control); }
  };

  PairPcmReservation &reservation_;
  const std::array<PairIdentity, 2> &identities_;
  const volatile std::sig_atomic_t &cancelled_;
  const std::filesystem::path &sound_root_;
  std::array<std::unique_ptr<snd_ctl_t, CloseControl>, 2> controls_;
  bool prepare_attempted_ = false;

  [[nodiscard]] Reply check(PairUnit unit) const {
    const auto live = reservation_.check(unit);
    if (!live) {
      return std::unexpected(
          StatusReadError{.code = live.error().code, .operation = live.error().operation});
    }
    return {};
  }

public:
  NativeClock(PairPcmReservation &reservation,
              const std::array<PairIdentity, 2> &identities,
              const volatile std::sig_atomic_t &cancelled,
              const std::filesystem::path &sound_root)
      : reservation_(reservation),
        identities_(identities),
        cancelled_(cancelled),
        sound_root_(sound_root) {}

  Reply open_controls() {
    for (std::size_t index = 0; index < controls_.size(); ++index) {
      const auto unit = index == 0 ? PairUnit::base : PairUnit::expansion;
      const auto live = check(unit);
      if (!live) { return live; }
      snd_ctl_t *raw = nullptr;
      const auto name = "hw:" + std::to_string(reservation_.card_indices()[index]);
      const int opened = snd_ctl_open(&raw, name.c_str(), SND_CTL_READONLY);
      controls_[index].reset(raw);
      if (opened < 0 || !controls_[index]) {
        return std::unexpected(StatusReadError{.code = opened < 0 ? opened : -EIO,
                                               .operation = "open paired status control"});
      }
      const auto still_live = check(unit);
      if (!still_live) { return still_live; }
    }
    return {};
  }

  std::expected<DeviceStatus, StatusReadError> read(PairUnit unit) override {
    const auto live = check(unit);
    if (!live) { return std::unexpected(live.error()); }
    const std::size_t index = unit == PairUnit::base ? 0 : 1;
    const auto verified = verify_pair_usb_identity(
        reservation_.card_indices()[index], identities_[index].serial, unit, sound_root_);
    if (!verified) {
      const auto error = verified.error();
      if (error.code == -ENOENT || error.code == -ENOTDIR) {
        return std::unexpected(
            StatusReadError{.code = -ENODEV, .operation = "paired USB identity disappeared"});
      }
      return std::unexpected(error);
    }
    const auto status = read_device_status_handle(controls_[index].get());
    const auto still_live = check(unit);
    if (!still_live) { return std::unexpected(still_live.error()); }
    return status;
  }

  Reply prepare_expansion(std::uint32_t rate) override {
    if (!prepare_attempted_) {
      prepare_attempted_ = true;
      const auto base = check(PairUnit::base);
      if (!base) { return base; }
    }
    const auto current = read(PairUnit::expansion);
    if (!current) { return std::unexpected(current.error()); }
    if (current->clock_source != ClockSource::internal || current->raw[6] != 0) {
      return std::unexpected(
          StatusReadError{.code = -EPERM, .operation = "paired EXP master changed before prepare"});
    }
    return prepare(reservation_.capture(PairUnit::expansion), rate);
  }

  [[nodiscard]] std::chrono::steady_clock::time_point now() const noexcept override {
    return std::chrono::steady_clock::now();
  }

  void pause(std::chrono::milliseconds delay) override { std::this_thread::sleep_for(delay); }

  [[nodiscard]] bool cancelled() const noexcept override { return cancelled_ != 0; }
};

}

std::expected<void, StatusReadError> verify_pair_usb_identity(
    int card, std::string_view serial, PairUnit unit, const std::filesystem::path &sound_root) {
  if (card < 0 || card > maximum_card_index || !valid_serial(serial)) {
    return std::unexpected(
        StatusReadError{.code = -EINVAL, .operation = "paired USB identity selector"});
  }
  std::error_code error;
  auto path =
      std::filesystem::canonical(sound_root / ("card" + std::to_string(card)) / "device", error);
  if (error) {
    return std::unexpected(
        StatusReadError{.code = -error.value(), .operation = "resolve paired USB identity"});
  }
  while (!path.empty()) {
    const auto vendor = text(path / "idVendor");
    if (vendor) {
      const auto product = text(path / "idProduct");
      const auto actual_serial = text(path / "serial");
      if (!product) { return std::unexpected(product.error()); }
      if (!actual_serial) { return std::unexpected(actual_serial.error()); }
      const std::string_view expected = unit == PairUnit::base ? "0120" : "0121";
      if (*vendor != "0582" || *product != expected || *actual_serial != serial) {
        return std::unexpected(
            StatusReadError{.code = -EXDEV, .operation = "paired USB vendor, product and serial"});
      }
      return {};
    }
    if (vendor.error().code != -ENOENT) { return std::unexpected(vendor.error()); }
    const auto parent = path.parent_path();
    if (path == parent) { break; }
    path = parent;
  }
  return std::unexpected(StatusReadError{.code = -ENODEV, .operation = "paired USB ancestor"});
}

std::expected<PairClockResult, PairClockFailure>
change_native_pair_clock(const std::array<PairIdentity, 2> &identities,
                         std::uint32_t rate,
                         const volatile std::sig_atomic_t &cancelled,
                         const std::filesystem::path &sound_root) {
  if (!valid_serial(identities[0].serial) || !valid_serial(identities[1].serial) ||
      identities[0].serial == identities[1].serial ||
      std::ranges::find(rates, rate) == rates.end()) {
    return std::unexpected(
        PairClockFailure{.cause = {.code = -EINVAL, .operation = "paired serials and rate"},
                         .recovery = std::nullopt});
  }
  auto reservation = PairPcmReservation::open({identities[0].card, identities[1].card});
  if (!reservation) {
    return std::unexpected(PairClockFailure{
        .cause = {.code = reservation.error().code, .operation = reservation.error().operation},
        .recovery = std::nullopt});
  }
  NativeClock transport(*reservation, identities, cancelled, sound_root);
  const auto controls = transport.open_controls();
  if (!controls) {
    return std::unexpected(PairClockFailure{.cause = controls.error(), .recovery = std::nullopt});
  }
  return change_pair_clock(transport, rate);
}

}
