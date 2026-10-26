// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PairClockNative.hpp"

#include "octa/DeviceStatus.hpp"
#include "octa/PairPcm.hpp"
#include "octa/StatusControl.hpp"

#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <csignal>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

extern "C" {
#include <stdlib.h>
}

namespace {
constexpr std::uint32_t original_rate = 48000;
constexpr std::uint32_t requested_rate = 96000;
constexpr std::array identities = {octa::PairIdentity{.card = "BASE", .serial = "BASE_SERIAL"},
                                   octa::PairIdentity{.card = "EXP", .serial = "EXP_SERIAL"}};
std::array<std::max_align_t, 4> tokens{};
std::array<std::max_align_t, 2> control_tokens{};
std::max_align_t parameter_token{};
volatile std::sig_atomic_t cancelled = 0;

snd_pcm_t *handle(std::size_t index) {
  return reinterpret_cast<snd_pcm_t *>(&tokens[index]);
}

snd_pcm_hw_params_t *parameters() {
  return reinterpret_cast<snd_pcm_hw_params_t *>(&parameter_token);
}

octa::DeviceStatus status(std::uint32_t rate, bool external) {
  constexpr unsigned int middle_shift = 8;
  constexpr unsigned int high_shift = 16;
  constexpr std::uint32_t mask = 255;
  const std::array<std::uint8_t, 8> raw = {1,
                                           0,
                                           0,
                                           static_cast<std::uint8_t>(rate & mask),
                                           static_cast<std::uint8_t>((rate >> middle_shift) & mask),
                                           static_cast<std::uint8_t>((rate >> high_shift) & mask),
                                           static_cast<std::uint8_t>(external),
                                           1};
  return *octa::decode_device_status(raw);
}

struct Peer {
  unsigned int opens = 0;
  unsigned int closes = 0;
  unsigned int status_reads = 0;
  unsigned int control_opens = 0;
  unsigned int control_closes = 0;
  unsigned int fail_control = 0;
  std::array<bool, 4> disconnected{};
  unsigned int live = 0;
  unsigned int allocated = 0;
  unsigned int freed = 0;
  int open_error = 0;
  bool invalid = false;
  bool failed = false;
  bool null_allocation = false;
  std::string_view fail_at;
  snd_pcm_state_t state = SND_PCM_STATE_OPEN;
  std::uint32_t rate = original_rate;
  std::uint32_t pending_rate = original_rate;
  std::array<octa::DeviceStatus, 2> clocks = {status(original_rate, true),
                                              status(original_rate, false)};
  std::vector<std::string_view> calls;
  std::vector<std::uint32_t> configured;
  std::function<void()> on_status;

  int step(snd_pcm_t *pcm, std::string_view call) {
    calls.push_back(call);
    if (live != 4 || pcm != handle(3)) {
      invalid = true;
      return -EINVAL;
    }
    if (!failed && fail_at == call) {
      failed = true;
      return -EIO;
    }
    return 0;
  }
};

Peer io;

struct Fixture {
  std::filesystem::path root;
  bool valid = false;

  Fixture() {
    auto name = std::to_array("/tmp/octa-pair-native-XXXXXX");
    const auto *created = mkdtemp(name.data());
    if (created == nullptr) { return; }
    root = created;
    std::error_code error;
    for (unsigned int unit = 0; unit < 2; ++unit) {
      const auto usb = root / (unit == 0 ? "base" : "exp");
      const auto card = root / "sound" / ("card" + std::to_string(unit));
      std::filesystem::create_directories(usb / "interface", error);
      if (error) { return; }
      std::filesystem::create_directories(card, error);
      if (error) { return; }
      std::filesystem::create_directory_symlink(usb / "interface", card / "device", error);
      if (error) { return; }
      if (!write(unit, "idVendor", "0582\n") ||
          !write(unit, "idProduct", unit == 0 ? "0120\n" : "0121\n") ||
          !write(unit, "serial", unit == 0 ? "BASE_SERIAL\n" : "EXP_SERIAL\n")) {
        return;
      }
    }
    valid = true;
  }

  Fixture(const Fixture &) = delete;
  Fixture &operator=(const Fixture &) = delete;
  Fixture(Fixture &&) = delete;
  Fixture &operator=(Fixture &&) = delete;

  ~Fixture() {
    if (root.empty()) { return; }
    std::error_code error;
    std::filesystem::remove_all(root, error);
  }

  [[nodiscard]] bool
  write(unsigned int unit, const std::filesystem::path &attribute, std::string_view value) const {
    std::ofstream file(root / (unit == 0 ? "base" : "exp") / attribute, std::ios::binary);
    file << value;
    file.close();
    return !file.fail();
  }

  [[nodiscard]] std::filesystem::path sound() const { return root / "sound"; }
};

bool require(bool condition, std::string_view message) {
  if (!condition) { std::cerr << "FAIL " << message << '\n'; }
  return condition;
}

bool clean() {
  return require(
      !io.invalid && io.live == 0 && io.closes == 4 && io.allocated == io.freed &&
          io.control_opens == io.control_closes && io.control_opens <= 2,
      "every fake PCM and parameter allocation is released; only EXP capture configured");
}

bool identities_match(const Fixture &fixture) {
  const auto base = octa::verify_pair_usb_identity(
      0, identities[0].serial, octa::PairUnit::base, fixture.sound());
  const auto expansion = octa::verify_pair_usb_identity(
      1, identities[1].serial, octa::PairUnit::expansion, fixture.sound());
  const auto swapped = octa::verify_pair_usb_identity(
      1, identities[1].serial, octa::PairUnit::base, fixture.sound());
  const auto wrong_serial = octa::verify_pair_usb_identity(
      0, identities[1].serial, octa::PairUnit::base, fixture.sound());
  return require(base && expansion && !swapped && swapped.error().code == -EXDEV && !wrong_serial &&
                     wrong_serial.error().code == -EXDEV,
                 "identity fixture requires vendor, base/EXP product and selected serial together");
}

bool invalid_identities(const Fixture &fixture) {
  constexpr auto embedded = std::to_array("BASE\0SERIAL");
  const std::array<std::string_view, 5> invalid = {
      "",
      "../BASE_SERIAL",
      "SERIAL name",
      "SERIAL\n",
      std::string_view(embedded.data(), embedded.size() - 1)};
  for (const auto serial : invalid) {
    io = {};
    auto selected = identities;
    selected[0].serial = serial;
    const auto result =
        octa::change_native_pair_clock(selected, requested_rate, cancelled, fixture.sound());
    if (!require(!result && result.error().cause.code == -EINVAL && io.opens == 0,
                 "invalid serials fail before PCM ownership or any device status read")) {
      return false;
    }
  }
  io = {};
  auto duplicate = identities;
  duplicate[1].serial = duplicate[0].serial;
  const auto repeated =
      octa::change_native_pair_clock(duplicate, requested_rate, cancelled, fixture.sound());
  const auto rate = octa::change_native_pair_clock(identities, 192000, cancelled, fixture.sound());
  return require(!repeated && !rate && io.opens == 0,
                 "duplicate serials and 192 kHz fail without ALSA");
}

bool attribute_guards(Fixture &fixture) {
  constexpr std::size_t oversized_serial = 65;

  struct Case {
    std::string_view name;
    std::string value;
    int error;
  };

  const std::array cases = {
      Case{.name = "idVendor", .value = "1234\n", .error = -EXDEV},
      Case{.name = "idProduct", .value = "0120\n", .error = -EXDEV},
      Case{.name = "serial", .value = "OTHER\n", .error = -EXDEV},
      Case{.name = "serial", .value = "\n", .error = -EPROTO},
      Case{.name = "serial", .value = std::string(oversized_serial, 'A'), .error = -EPROTO}};
  for (const auto &item : cases) {
    io = {};
    if (!fixture.write(1, item.name, item.value)) { return false; }
    const auto result =
        octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
    if (!fixture.write(1, "idVendor", "0582\n") || !fixture.write(1, "idProduct", "0121\n") ||
        !fixture.write(1, "serial", "EXP_SERIAL\n")) {
      return false;
    }
    if (!require(!result && result.error().cause.code == item.error &&
                     !result.error().change_attempted && io.status_reads == 1 && io.calls.empty(),
                 "USB attribute failures stop before EXP configuration") ||
        !clean()) {
      return false;
    }
  }
  return true;
}

bool native_success(const Fixture &fixture) {
  io = {};
  const auto result =
      octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
  const std::vector<std::string_view> expected = {"allocate",
                                                  "any",
                                                  "access",
                                                  "format",
                                                  "channels",
                                                  "rate",
                                                  "period",
                                                  "buffer",
                                                  "apply",
                                                  "prepare"};
  if (!require(result && result->changed && result->previous_rate == original_rate &&
                   result->rate == requested_rate && io.calls == expected &&
                   io.configured == std::vector{requested_rate} && io.status_reads == 9,
               "native path checks identities and status, configures exact EXP capture, then "
               "verifies the pair") ||
      !clean()) {
    return false;
  }
  io = {};
  const auto unchanged =
      octa::change_native_pair_clock(identities, original_rate, cancelled, fixture.sound());
  return require(unchanged && !unchanged->changed && io.calls.empty() && io.status_reads == 4,
                 "already-coherent native pair does not touch PCM parameters") &&
         clean();
}

bool native_errors(const Fixture &fixture) {
  constexpr std::array<std::string_view, 10> failures = {"allocate",
                                                         "any",
                                                         "access",
                                                         "format",
                                                         "channels",
                                                         "rate",
                                                         "period",
                                                         "buffer",
                                                         "apply",
                                                         "prepare"};
  for (const auto operation : failures) {
    io = {};
    io.fail_at = operation;
    const auto result =
        octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
    if (!require(
            !result && result.error().cause.code == -EIO && result.error().change_attempted &&
                !result.error().recovery && io.failed && io.rate == original_rate,
            "every configuration failure returns the original error after verified recovery") ||
        !clean()) {
      return false;
    }
    const auto failed = std::ranges::find(io.calls, operation);
    if (!require(failed != io.calls.end() && failed + 1 != io.calls.end() &&
                     (*(failed + 1) == "allocate" || *(failed + 1) == "drop"),
                 "no later forward constraints execute after an ALSA failure")) {
      return false;
    }
  }
  io = {};
  io.null_allocation = true;
  const auto null =
      octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
  return require(!null && null.error().cause.code == -EIO && !null.error().recovery,
                 "success-with-null parameter allocation is rejected and recovered") &&
         clean();
}

bool busy_and_disconnect(const Fixture &fixture) {
  io = {};
  io.open_error = -EBUSY;
  const auto busy =
      octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
  if (!require(!busy && busy.error().cause.code == -EBUSY && !busy.error().change_attempted &&
                   io.status_reads == 0 && io.calls.empty() && io.closes == 0,
               "busy ownership stops before sysfs or status access")) {
    return false;
  }
  io = {};
  io.state = SND_PCM_STATE_DISCONNECTED;
  const auto lost =
      octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
  return require(!lost && lost.error().cause.code == -ENODEV && !lost.error().change_attempted &&
                     !lost.error().recovery && io.calls.empty(),
                 "disconnected PCM never receives parameter configuration") &&
         clean();
}

bool retained_controls_and_pcm(const Fixture &fixture) {
  for (unsigned int failed = 1; failed <= 2; ++failed) {
    io = {};
    io.fail_control = failed;
    const auto result =
        octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
    if (!require(
            !result && result.error().cause.code == -EACCES && !result.error().change_attempted &&
                io.control_opens == failed - 1 && io.status_reads == 0 && io.calls.empty(),
            "control acquisition failure closes previous controls and all four reservations") ||
        !clean()) {
      return false;
    }
  }
  for (const auto direction : {0U, 1U, 2U, 3U}) {
    io = {};
    io.on_status = [direction] {
      if (io.status_reads == 4) { io.disconnected[direction] = true; }
    };
    const auto result =
        octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
    if (!require(
            !result && result.error().cause.code == -ENODEV &&
                std::ranges::find(io.configured, requested_rate) == io.configured.end() &&
                io.control_opens == 2 && io.status_reads <= 6,
            "a disconnected held direction cannot be replaced by reopening a reused card number") ||
        !clean()) {
      return false;
    }
  }
  return true;
}

bool release_failures(const Fixture &fixture) {
  struct Case {
    snd_pcm_state_t state;
    std::string_view operation;
  };

  constexpr std::array cases = {Case{.state = SND_PCM_STATE_PREPARED, .operation = "drop"},
                                Case{.state = SND_PCM_STATE_SETUP, .operation = "free"}};
  for (const auto &item : cases) {
    io = {};
    io.state = item.state;
    io.fail_at = item.operation;
    const auto result =
        octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
    if (!require(
            !result && result.error().cause.code == -EIO && !result.error().recovery &&
                io.configured == std::vector{original_rate} && io.failed,
            "drop/free errors stop forward preparation and allow verified old-rate recovery") ||
        !clean()) {
      return false;
    }
  }
  return true;
}

bool interruption_after_write(Fixture &fixture) {
  for (const auto replacement : {false, true}) {
    io = {};
    io.on_status = [&fixture, replacement] {
      if (io.status_reads != 6) { return; }
      if (replacement) {
        if (!fixture.write(1, "serial", "REPLACEMENT\n")) { io.invalid = true; }
      } else {
        io.disconnected[0] = true;
      }
    };
    const auto result =
        octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
    if (!fixture.write(1, "serial", "EXP_SERIAL\n")) { return false; }
    const auto expected =
        replacement ? std::vector{requested_rate} : std::vector{requested_rate, original_rate};
    const int error = replacement ? -EXDEV : -ENODEV;
    if (!require(!result && result.error().cause.code == error &&
                     result.error().recovery.has_value() && io.configured == expected,
                 "a lost base permits EXP restoration; an identity change prohibits writing the "
                 "replacement") ||
        !clean()) {
      return false;
    }
  }
  return true;
}

bool native_cancellation(const Fixture &fixture) {
  io = {};
  io.on_status = [] {
    if (io.status_reads == 6) { cancelled = 1; }
  };
  const auto result =
      octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
  cancelled = 0;
  return require(!result && result.error().cause.code == -EINTR && !result.error().recovery &&
                     io.configured == std::vector{requested_rate, original_rate},
                 "native cancellation flag cannot suppress verified old-rate recovery") &&
         clean();
}

bool changed_before_prepare(Fixture &fixture) {
  io = {};
  io.on_status = [&fixture] {
    if (io.status_reads == 4 && !fixture.write(1, "serial", "REPLACEMENT\n")) { io.invalid = true; }
  };
  const auto replaced =
      octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
  if (!fixture.write(1, "serial", "EXP_SERIAL\n")) { return false; }
  if (!require(!replaced && replaced.error().cause.code == -EXDEV &&
                   replaced.error().recovery.has_value() && io.calls.empty() &&
                   io.status_reads == 4,
               "identity is checked again before prepare and prevents writes to a replacement") ||
      !clean()) {
    return false;
  }
  io = {};
  io.on_status = [] {
    if (io.status_reads == 5) { io.clocks[1].raw[6] = 2; }
  };
  const auto changed =
      octa::change_native_pair_clock(identities, requested_rate, cancelled, fixture.sound());
  return require(!changed && changed.error().cause.code == -EPERM &&
                     changed.error().recovery.has_value() && io.calls.empty(),
                 "fresh master setting bits prohibit prepare and guessed recovery") &&
         clean();
}

}

namespace octa {
std::expected<PairPcmReservation, PairPcmError>
PairPcmReservation::open(const std::array<std::string_view, 2> &card_ids) {
  ++io.opens;
  if (io.open_error < 0) {
    return std::unexpected(PairPcmError{.code = io.open_error, .operation = "test reserve"});
  }
  if (card_ids != std::array{identities[0].card, identities[1].card}) { io.invalid = true; }
  PairPcmReservation result;
  result.cards_ = {0, 1};
  for (std::size_t index = 0; index < 4; ++index) {
    result.handles_[index].reset(handle(index));
    ++io.live;
  }
  return result;
}

void PairPcmReservation::ClosePcm::operator()(snd_pcm_t * /*pcm*/) const noexcept {
  ++io.closes;
  --io.live;
}

snd_pcm_t *PairPcmReservation::capture(PairUnit unit) const noexcept {
  return handles_[unit == PairUnit::base ? 1 : 3].get();
}

const std::array<int, 2> &PairPcmReservation::card_indices() const noexcept {
  return cards_;
}

std::expected<void, PairPcmError> PairPcmReservation::check(PairUnit unit) const {
  const std::size_t start = unit == PairUnit::base ? 0 : 2;
  if (!handles_[start] || !handles_[start + 1]) {
    return std::unexpected(
        PairPcmError{.code = -EINVAL, .operation = "test missing PCM ownership"});
  }
  if (io.disconnected[start] || io.disconnected[start + 1] ||
      (unit == PairUnit::expansion && io.state == SND_PCM_STATE_DISCONNECTED)) {
    return std::unexpected(
        PairPcmError{.code = -ENODEV, .operation = "test pinned PCM disconnect"});
  }
  if (io.live != 4) { io.invalid = true; }
  return {};
}

std::expected<DeviceStatus, StatusReadError> read_device_status_handle(snd_ctl_t *control) {
  const auto *base = reinterpret_cast<snd_ctl_t *>(control_tokens.data());
  const auto *expansion = reinterpret_cast<snd_ctl_t *>(&control_tokens[1]);
  if (io.live != 4 || (control != base && control != expansion) || io.control_opens != 2 ||
      io.control_closes != 0) {
    io.invalid = true;
  }
  ++io.status_reads;
  if (io.on_status) { io.on_status(); }
  return io.clocks[control == base ? 0 : 1];
}
}

extern "C" int snd_ctl_open(snd_ctl_t **ctl, const char *name, int mode) {
  if (io.live != 4 || io.control_opens >= 2 || mode != SND_CTL_READONLY ||
      std::string_view(name) != (io.control_opens == 0 ? "hw:0" : "hw:1")) {
    io.invalid = true;
    return -EINVAL;
  }
  if (io.control_opens + 1 == io.fail_control) { return -EACCES; }
  *ctl = reinterpret_cast<snd_ctl_t *>(&control_tokens[io.control_opens]);
  ++io.control_opens;
  return 0;
}

extern "C" int snd_ctl_close(snd_ctl_t * /*control*/) {
  if (io.live != 4) { io.invalid = true; }
  ++io.control_closes;
  return 0;
}

extern "C" snd_pcm_state_t snd_pcm_state(snd_pcm_t *pcm) {
  if (pcm != handle(3) || io.live != 4) { io.invalid = true; }
  return io.state;
}

extern "C" int snd_pcm_drop(snd_pcm_t *pcm) {
  const auto result = io.step(pcm, "drop");
  if (result == 0) { io.state = SND_PCM_STATE_SETUP; }
  return result;
}

extern "C" int snd_pcm_hw_free(snd_pcm_t *pcm) {
  const auto result = io.step(pcm, "free");
  if (result == 0) { io.state = SND_PCM_STATE_OPEN; }
  return result;
}

extern "C" int snd_pcm_hw_params_malloc(snd_pcm_hw_params_t **ptr) {
  const auto result = io.step(handle(3), "allocate");
  if (result < 0) { return result; }
  if (io.null_allocation) {
    io.null_allocation = false;
    return 0;
  }
  ++io.allocated;
  *ptr = parameters();
  return 0;
}

extern "C" void snd_pcm_hw_params_free(snd_pcm_hw_params_t *obj) {
  if (obj != parameters() || io.allocated != io.freed + 1) { io.invalid = true; }
  ++io.freed;
}

extern "C" int snd_pcm_hw_params_any(snd_pcm_t *pcm, snd_pcm_hw_params_t * /*parameters*/) {
  return io.step(pcm, "any");
}

extern "C" int snd_pcm_hw_params_set_access(snd_pcm_t *pcm,
                                            snd_pcm_hw_params_t * /*parameters*/,
                                            snd_pcm_access_t access) {
  if (access != SND_PCM_ACCESS_RW_INTERLEAVED) { io.invalid = true; }
  return io.step(pcm, "access");
}

extern "C" int snd_pcm_hw_params_set_format(snd_pcm_t *pcm,
                                            snd_pcm_hw_params_t * /*parameters*/,
                                            snd_pcm_format_t val) {
  if (val != SND_PCM_FORMAT_S24_3LE) { io.invalid = true; }
  return io.step(pcm, "format");
}

extern "C" int snd_pcm_hw_params_set_channels(snd_pcm_t *pcm,
                                              snd_pcm_hw_params_t * /*parameters*/,
                                              unsigned int val) {
  if (val != 12) { io.invalid = true; }
  return io.step(pcm, "channels");
}

extern "C" int snd_pcm_hw_params_set_rate(snd_pcm_t *pcm,
                                          snd_pcm_hw_params_t * /*parameters*/,
                                          unsigned int val,
                                          int dir) {
  if (dir != 0 || (val != requested_rate && val != original_rate)) { io.invalid = true; }
  io.pending_rate = val;
  return io.step(pcm, "rate");
}

extern "C" int snd_pcm_hw_params_set_period_size(snd_pcm_t *pcm,
                                                 snd_pcm_hw_params_t * /*parameters*/,
                                                 snd_pcm_uframes_t val,
                                                 int dir) {
  constexpr snd_pcm_uframes_t expected = 1024;
  if (val != expected || dir != 0) { io.invalid = true; }
  return io.step(pcm, "period");
}

extern "C" int snd_pcm_hw_params_set_buffer_size(snd_pcm_t *pcm,
                                                 snd_pcm_hw_params_t * /*parameters*/,
                                                 snd_pcm_uframes_t val) {
  constexpr snd_pcm_uframes_t expected = 8192;
  if (val != expected) { io.invalid = true; }
  return io.step(pcm, "buffer");
}

extern "C" int snd_pcm_hw_params(snd_pcm_t *pcm, snd_pcm_hw_params_t * /*parameters*/) {
  io.configured.push_back(io.pending_rate);
  io.rate = io.pending_rate;
  io.clocks = {status(io.rate, true), status(io.rate, false)};
  io.state = SND_PCM_STATE_PREPARED;
  return io.step(pcm, "apply");
}

extern "C" int snd_pcm_prepare(snd_pcm_t *pcm) {
  return io.step(pcm, "prepare");
}

int main() {
  Fixture fixture;
  if (!require(fixture.valid, "private sysfs fixture creation") || !identities_match(fixture) ||
      !invalid_identities(fixture) || !attribute_guards(fixture) || !native_success(fixture) ||
      !native_errors(fixture) || !busy_and_disconnect(fixture) ||
      !changed_before_prepare(fixture) || !retained_controls_and_pcm(fixture) ||
      !release_failures(fixture) || !interruption_after_write(fixture) ||
      !native_cancellation(fixture)) {
    return 1;
  }
  std::cout << "PASS native paired clock identity/prepare/error paths; filesystem and ALSA "
               "fixtures only\n";
}
