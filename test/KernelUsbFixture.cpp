// SPDX-License-Identifier: GPL-2.0-only
// Deterministic synthetic USB model, restricted to the private sanitizer VM.
#include <algorithm>
#include <array>
#include <asm-generic/errno.h>
#include <cerrno>
#include <charconv>
#include <csignal>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <initializer_list>
#include <iostream>
#include <limits>
#include <linux/prctl.h>
#include <linux/usb/ch9.h>
#include <linux/usb/raw_gadget.h>
#include <sound/asound.h>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <system_error>
#include <unistd.h>
#include <vector>

// POSIX process control uses the native C declarations and wait-status macros.
extern "C" {
#include <signal.h>
#include <stdlib.h>
#include <sys/wait.h>
}

namespace {
using Bytes = std::vector<std::uint8_t>;
constexpr std::uint8_t byte_mask = 255;
constexpr std::uint8_t pcm_bits = 24;
constexpr std::uint8_t playback_ep = 0x05;
constexpr std::uint8_t capture_ep = 0x85;
constexpr std::uint8_t midi_in_ep = 0x86;
constexpr std::uint8_t status_ep = 0x82;
constexpr std::uint8_t auxiliary_ep = 0x81;
constexpr std::uint8_t status_rate_high = 0xbb;
constexpr std::uint8_t interface_in = USB_DIR_IN | USB_RECIP_INTERFACE;
constexpr std::uint8_t vendor_in = USB_DIR_IN | USB_TYPE_VENDOR;
constexpr std::uint8_t implicit_feedback =
    USB_ENDPOINT_SYNC_ASYNC | USB_ENDPOINT_USAGE_IMPLICIT_FB | USB_ENDPOINT_XFER_ISOC;
constexpr std::uint8_t async_playback = USB_ENDPOINT_SYNC_ASYNC | USB_ENDPOINT_XFER_ISOC;
constexpr std::uint8_t sentinel = 0xa5;
constexpr unsigned int pcm_rate_hz = 48000;

enum class StatusMode : std::uint8_t {
  normal,
  empty,
  short_packet,
  invalid_phase,
  zero_rate,
  missing_zero,
  missing_one,
  timeout
};

struct Scenario {
  std::string_view name;
  StatusMode mode;
  int error;
  std::uint16_t product_id = 0x0120;
};

constexpr std::array scenarios = {
    Scenario{.name = "normal", .mode = StatusMode::normal, .error = 0},
    Scenario{.name = "empty", .mode = StatusMode::empty, .error = EIO},
    Scenario{.name = "short", .mode = StatusMode::short_packet, .error = EIO},
    Scenario{.name = "phase", .mode = StatusMode::invalid_phase, .error = EPROTO},
    Scenario{.name = "zero-rate", .mode = StatusMode::zero_rate, .error = ENODATA},
    Scenario{.name = "missing-zero", .mode = StatusMode::missing_zero, .error = EAGAIN},
    Scenario{.name = "missing-one", .mode = StatusMode::missing_one, .error = EAGAIN},
    Scenario{.name = "timeout", .mode = StatusMode::timeout, .error = ETIMEDOUT},
    Scenario{.name = "disconnect", .mode = StatusMode::timeout, .error = 0},
    Scenario{.name = "expansion", .mode = StatusMode::normal, .error = 0, .product_id = 0x0121}};

[[noreturn]] void fail(const char *message) {
  std::perror(message);
  std::_Exit(1);
}

void require(bool condition, const char *message) {
  if (!condition) {
    std::cerr << "FAIL " << message << '\n';
    std::_Exit(1);
  }
}

int checked(int result, const char *message) {
  if (result < 0) { fail(message); }
  return result;
}

Scenario scenario(std::string_view name) {
  const auto *const found = std::ranges::find(scenarios, name, &Scenario::name);
  require(found != scenarios.end(), "Unknown status scenario");
  return *found;
}

void guard() {
  utsname system{};
  checked(uname(&system), "uname");
  require(std::string_view(system.release).ends_with("-octa-kasan-vm"),
          "Private VM kernel required");
}

void append(Bytes &bytes, std::initializer_list<std::uint8_t> tail) {
  bytes.insert(bytes.end(), tail.begin(), tail.end());
}

void interface(Bytes &bytes, std::uint8_t number, std::uint8_t alt, std::uint8_t endpoints) {
  append(bytes, {9, 4, number, alt, endpoints, USB_CLASS_VENDOR_SPEC, 2, 0, 0});
}

void endpoint(Bytes &bytes,
              std::uint8_t address,
              std::uint8_t attributes,
              unsigned int packet,
              std::uint8_t interval) {
  append(bytes,
         {7,
          5,
          address,
          attributes,
          static_cast<std::uint8_t>(packet & byte_mask),
          static_cast<std::uint8_t>(packet >> 8),
          interval});
}

void audio_interface(Bytes &bytes, bool input) {
  constexpr std::array<unsigned int, 4> rates{44100, 48000, 96000, 192000};
  constexpr std::array<unsigned int, 4> playback{212, 212, 392, 312};
  constexpr std::array<unsigned int, 4> capture{256, 256, 468, 312};
  const std::uint8_t number = input ? 1 : 0;
  const auto &packets = input ? capture : playback;
  const auto address = input ? capture_ep : playback_ep;
  const auto attributes = input ? implicit_feedback : async_playback;
  const std::uint8_t normal_channels = input ? 12 : 10;
  interface(bytes, number, 0, 0);
  for (std::uint8_t alt = 1; alt <= 4; ++alt) {
    interface(bytes, number, alt, 1);
    append(bytes, {7, USB_DT_CS_INTERFACE, 1, 1, 0, 1, 0});
    const std::uint8_t channels = alt == 4 ? 4 : normal_channels;
    const auto rate = rates[alt - 1U];
    append(bytes,
           {11,
            USB_DT_CS_INTERFACE,
            2,
            1,
            channels,
            3,
            pcm_bits,
            1,
            static_cast<std::uint8_t>(rate & byte_mask),
            static_cast<std::uint8_t>((rate >> 8) & byte_mask),
            static_cast<std::uint8_t>(rate >> 16)});
    endpoint(bytes, address, attributes, packets[alt - 1U], 1);
    append(bytes, {7, USB_DT_CS_ENDPOINT, 1, 0, 0, 0, 0});
  }
}

Bytes configuration() {
  Bytes bytes{9, 2, 0, 0, 5, 1, 0, USB_CONFIG_ATT_ONE, 1};
  audio_interface(bytes, false);
  audio_interface(bytes, true);
  // MIDI packet sizes are legal model values, not recovered hardware descriptors.
  for (std::uint8_t alt = 0; alt < 2; ++alt) {
    interface(bytes, 2, alt, 2);
    endpoint(bytes, 6, alt != 0 ? 3 : 2, alt != 0 ? 64 : 512, alt != 0 ? 1 : 0);
    endpoint(bytes, midi_in_ep, alt != 0 ? 3 : 2, alt != 0 ? 64 : 512, alt != 0 ? 1 : 0);
  }
  interface(bytes, 3, 0, 0);
  interface(bytes, 3, 1, 1);
  endpoint(bytes, status_ep, 3, 8, 1);
  interface(bytes, 4, 0, 0);
  for (std::uint8_t alt = 1; alt <= 4; ++alt) {
    interface(bytes, 4, alt, 1);
    endpoint(bytes, auxiliary_ep, 3, 8, alt);
  }
  bytes[2] = static_cast<std::uint8_t>(bytes.size() & byte_mask);
  bytes[3] = static_cast<std::uint8_t>(bytes.size() >> 8);
  return bytes;
}

Bytes descriptor(std::uint16_t value, const Scenario &test) {
  switch (value >> 8) {
    case USB_DT_DEVICE: {
      const std::array<std::uint8_t, 18> device{
          18,
          1,
          0,
          2,
          0,
          0,
          0,
          64,
          0x82,
          5,
          static_cast<std::uint8_t>(test.product_id & byte_mask),
          static_cast<std::uint8_t>(test.product_id >> 8),
          0,
          2,
          1,
          2,
          3,
          1};
      return {device.begin(), device.end()};
    }
    case USB_DT_CONFIG: return configuration();
    case USB_DT_DEVICE_QUALIFIER: return {10, 6, 0, 2, 0, 0, 0, 64, 1, 0};
    case USB_DT_STRING: {
      if ((value & byte_mask) == 0) { return {4, 3, 9, 4}; }
      constexpr std::array<std::string_view, 3> strings{
          "OCTA test fixture", "Synthetic OCTA-CAPTURE", "VM-ONLY-1"};
      const auto index = static_cast<unsigned int>(value & byte_mask);
      if (index > strings.size()) { return {}; }
      Bytes bytes{static_cast<std::uint8_t>(2 + (strings[index - 1].size() * 2)), 3};
      for (const char ch : strings[index - 1]) {
        append(bytes, {static_cast<std::uint8_t>(ch), 0});
      }
      return bytes;
    }
    default: return {};
  }
}

struct Io {
  std::uint16_t ep{};
  std::uint16_t flags{};
  std::uint32_t length{};
  std::array<std::uint8_t, 2048> data{};
};

struct Event {
  std::uint32_t type{};
  std::uint32_t length{8};
  std::array<std::uint8_t, 8> data{};
};

static_assert(offsetof(Io, data) == sizeof(usb_raw_ep_io));
static_assert(offsetof(Event, data) == sizeof(usb_raw_event));

class Gadget {
public:
  explicit Gadget(const Scenario &test)
      : fd_(checked(open("/dev/raw-gadget", O_RDWR | O_CLOEXEC), "raw gadget")), test_(test) {}

  void run() {
    usb_raw_init init{};
    std::memcpy(init.driver_name, "dummy_udc", sizeof("dummy_udc"));
    std::memcpy(init.device_name, "dummy_udc.0", sizeof("dummy_udc.0"));
    init.speed = USB_SPEED_HIGH;
    checked(ioctl(fd_, USB_RAW_IOCTL_INIT, &init), "INIT");
    checked(ioctl(fd_, USB_RAW_IOCTL_RUN, 0UL), "RUN");
    for (;;) {
      Event event{};
      checked(ioctl(fd_, USB_RAW_IOCTL_EVENT_FETCH, &event), "EVENT");
      if (event.type != USB_RAW_EVENT_CONTROL) { continue; }
      require(event.length == sizeof(usb_ctrlrequest), "Control event size");
      usb_ctrlrequest request{};
      std::memcpy(&request, event.data.data(), sizeof(request));
      std::cout << "CONTROL " << static_cast<unsigned int>(request.bRequestType) << ' '
                << static_cast<unsigned int>(request.bRequest) << ' ' << request.wValue << ' '
                << request.wIndex << ' ' << request.wLength << '\n'
                << std::flush;
      control(request);
    }
  }

private:
  const int fd_;
  const Scenario test_;
  std::array<std::uint8_t, 5> alts_{};
  int status_ep_{-1};
  pid_t writer_{-1};

  void reply(const usb_ctrlrequest &request, std::span<const std::uint8_t> data) const {
    Io io{};
    const auto size = std::min<std::size_t>(data.size(), request.wLength);
    require(size <= io.data.size(), "Bounded control reply");
    std::copy_n(data.begin(), size, io.data.begin());
    io.length = static_cast<std::uint32_t>(size);
    checked(ioctl(fd_, USB_RAW_IOCTL_EP0_WRITE, &io), "EP0 WRITE");
  }

  void ack() const {
    Io io{};
    checked(ioctl(fd_, USB_RAW_IOCTL_EP0_READ, &io), "EP0 ACK");
  }

  void status(bool enable) {
    if (status_ep_ >= 0) {
      checked(kill(writer_, SIGTERM), "Stop status writer");
      int result = 0;
      checked(waitpid(writer_, &result, 0), "Reap status writer");
      require(WIFSIGNALED(result) && WTERMSIG(result) == SIGTERM, "Status writer stopped");
      checked(ioctl(fd_, USB_RAW_IOCTL_EP_DISABLE, static_cast<unsigned long>(status_ep_)),
              "DISABLE");
      status_ep_ = -1;
    }
    if (!enable) { return; }
    usb_endpoint_descriptor endpoint{};
    endpoint.bLength = USB_DT_ENDPOINT_SIZE;
    endpoint.bDescriptorType = USB_DT_ENDPOINT;
    endpoint.bEndpointAddress = status_ep;
    endpoint.bmAttributes = USB_ENDPOINT_XFER_INT;
    endpoint.wMaxPacketSize = 8;
    endpoint.bInterval = 1;
    status_ep_ = checked(ioctl(fd_, USB_RAW_IOCTL_EP_ENABLE, &endpoint), "ENABLE STATUS");
    const pid_t parent = getpid();
    writer_ = checked(fork(), "Fork status writer");
    if (writer_ == 0) {
      checked(prctl(PR_SET_PDEATHSIG, SIGTERM), "Parent death signal");
      require(getppid() == parent, "Gadget parent still alive");
      write_status();
    }
  }

  [[noreturn]] void write_status() const {
    if (test_.mode == StatusMode::timeout) {
      for (;;) { pause(); }
    }
    Io io{};
    io.ep = static_cast<std::uint16_t>(status_ep_);
    io.length = 8;
    if (test_.mode == StatusMode::empty) { io.length = 0; }
    if (test_.mode == StatusMode::short_packet) { io.length = 7; }
    io.data[3] = 0x80;
    io.data[4] = status_rate_high;
    if (test_.mode == StatusMode::zero_rate) {
      io.data[3] = 0;
      io.data[4] = 0;
    }
    for (;;) {
      if (test_.mode == StatusMode::invalid_phase) { io.data[0] = 2; }
      if (test_.mode == StatusMode::missing_zero) { io.data[0] = 1; }
      if (test_.mode == StatusMode::missing_one) { io.data[0] = 0; }
      const int result = checked(ioctl(fd_, USB_RAW_IOCTL_EP_WRITE, &io), "STATUS WRITE");
      require(static_cast<std::uint32_t>(result) == io.length, "Complete modeled status packet");
      io.data[0] ^= 1;
    }
  }

  void set_interface(const usb_ctrlrequest &request) {
    const bool idle_audio = request.wIndex < 2 && request.wValue == 0;
    const bool midi = request.wIndex == 2 && request.wValue <= 1;
    const bool status_interface = request.wIndex == 3 && request.wValue <= 1;
    const bool auxiliary = request.wIndex == 4 && request.wValue == 0;
    if (!(idle_audio || midi || status_interface || auxiliary)) {
      if (request.wIndex < 2 && request.wValue >= 1 && request.wValue <= 4) {
        std::cout << "OCTA_VM_PCM_ALT_STALL " << request.wIndex << ' ' << request.wValue << '\n'
                  << std::flush;
      }
      checked(ioctl(fd_, USB_RAW_IOCTL_EP0_STALL, 0UL), "Unsupported alternate");
      return;
    }
    if (status_interface) { status(request.wValue == 1); }
    alts_[request.wIndex] = static_cast<std::uint8_t>(request.wValue);
    ack();
  }

  void control(const usb_ctrlrequest &request) {
    if (request.bRequestType == 0x80 && request.bRequest == USB_REQ_GET_DESCRIPTOR) {
      const auto bytes = descriptor(request.wValue, test_);
      if (!bytes.empty()) {
        reply(request, bytes);
        return;
      }
    } else if (request.bRequestType == 0 && request.bRequest == USB_REQ_SET_CONFIGURATION &&
               request.wValue == 1) {
      checked(ioctl(fd_, USB_RAW_IOCTL_CONFIGURE, 0UL), "CONFIGURE");
      ack();
      return;
    } else if (request.bRequestType == 1 && request.bRequest == USB_REQ_SET_INTERFACE &&
               request.wIndex < alts_.size()) {
      set_interface(request);
      return;
    } else if (request.bRequestType == interface_in && request.bRequest == USB_REQ_GET_INTERFACE &&
               request.wIndex < alts_.size()) {
      reply(request, std::span(alts_).subspan(request.wIndex, 1));
      return;
    } else if (request.bRequestType == 0x80 && request.bRequest == USB_REQ_GET_CONFIGURATION) {
      constexpr std::array<std::uint8_t, 1> configured{1};
      reply(request, configured);
      return;
    } else if ((request.bRequestType & USB_TYPE_MASK) == 0 &&
               request.bRequest == USB_REQ_GET_STATUS && (request.bRequestType & USB_DIR_IN) != 0) {
      constexpr std::array<std::uint8_t, 2> zero{};
      reply(request, zero);
      return;
    } else if (request.bRequestType == vendor_in && request.bRequest == 3 && request.wIndex == 0) {
      if (request.wValue == 1) {
        constexpr std::array<std::uint8_t, 4> clock{0x80, 0xbb, 0, 0};
        reply(request, clock);
        return;
      }
      if (request.wValue == 2) {
        constexpr std::array<std::uint8_t, 1> ready{1};
        reply(request, ready);
        return;
      }
    }
    checked(ioctl(fd_, USB_RAW_IOCTL_EP0_STALL, 0UL), "STALL");
  }
};

int status_control(snd_ctl_elem_info &info) {
  const int fd = checked(open("/dev/snd/controlC0", O_RDWR | O_CLOEXEC), "ALSA control");
  snd_ctl_card_info card{};
  checked(ioctl(fd, SNDRV_CTL_IOCTL_CARD_INFO, &card), "Card INFO");
  require(std::string_view(reinterpret_cast<const char *>(card.longname)).contains("dummy_hcd"),
          "Virtual card identity");
  info.id.iface = SNDRV_CTL_ELEM_IFACE_CARD;
  constexpr std::string_view name = "OCTA-CAPTURE Status";
  std::memcpy(info.id.name, name.data(), name.size());
  checked(ioctl(fd, SNDRV_CTL_IOCTL_ELEM_INFO, &info), "Status INFO");
  require(info.type == SNDRV_CTL_ELEM_TYPE_BYTES && info.count == 8, "Status metadata");
  require(info.access == (SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_VOLATILE),
          "Read-only volatile status");
  return fd;
}

void read_status(const Scenario &test, int error_output = -1) {
  snd_ctl_elem_info info{};
  const int fd = status_control(info);
  constexpr int read_count = 20;
  const bool expect_error = test.error != 0 || error_output >= 0;
  for (int index = 0; index < (expect_error ? 1 : read_count); ++index) {
    snd_ctl_elem_value value{};
    value.id = info.id;
    std::ranges::fill(value.value.bytes.data, sentinel);
    const int result = ioctl(fd, SNDRV_CTL_IOCTL_ELEM_READ, &value);
    const int actual_error = errno;
    if (expect_error) {
      if (result != -1 || (error_output < 0 && actual_error != test.error)) {
        std::cerr << "Status error result=" << result << " errno=" << actual_error
                  << " expected=" << test.error << '\n';
      }
      require(result == -1 && (error_output >= 0 || actual_error == test.error),
              "Failed ALSA status read and exact expected error");
      require(
          std::ranges::all_of(value.value.bytes.data, [](auto byte) { return byte == sentinel; }),
          "Failed reads leave userspace output untouched");
      if (error_output >= 0) {
        require(write(error_output, &actual_error, sizeof(actual_error)) == sizeof(actual_error),
                "Report ALSA status error to parent");
      }
      continue;
    }
    checked(result, "Status READ");
    constexpr std::array<std::uint8_t, 8> expected{1, 0, 0, 0x80, 0xbb, 0, 0, 0};
    require(std::equal(expected.begin(), expected.end(), value.value.bytes.data), "Status bytes");
  }
  checked(close(fd), "close ALSA");
  if (expect_error) {
    std::cout << "OCTA_VM_STATUS_ERROR_PASS " << test.name << '\n';
  } else {
    std::puts("OCTA_VM_STATUS_READ_PASS");
  }
}

struct Setting {
  const char *path;
  std::string_view value;
};

void write_setting(const Setting &setting) {
  const int fd = checked(open(setting.path, O_WRONLY | O_CLOEXEC), setting.path);
  require(write(fd, setting.value.data(), setting.value.size()) ==
              static_cast<ssize_t>(setting.value.size()),
          "Write complete VM fault setting");
  checked(close(fd), "Close VM fault setting");
}

void allocation_status() {
  snd_ctl_elem_info info{};
  const int control = status_control(info);
  snd_ctl_elem_value value{};
  value.id = info.id;
  checked(ioctl(control, SNDRV_CTL_IOCTL_ELEM_READ, &value), "Warm status before allocation fault");
  std::ranges::fill(value.value.bytes.data, sentinel);
  const int task = checked(open("/proc/self/make-it-fail", O_WRONLY | O_CLOEXEC), "Task filter");
  constexpr std::array settings = {
      Setting{.path = "/sys/kernel/debug/failslab/task-filter", .value = "Y"},
      Setting{.path = "/sys/kernel/debug/failslab/cache-filter", .value = "Y"},
      Setting{.path = "/sys/kernel/debug/failslab/ignore-gfp-wait", .value = "N"},
      Setting{.path = "/sys/kernel/debug/failslab/interval", .value = "1"},
      Setting{.path = "/sys/kernel/debug/failslab/times", .value = "1"},
      Setting{.path = "/sys/kernel/debug/failslab/space", .value = "0"},
      Setting{.path = "/sys/kernel/debug/failslab/verbose", .value = "2"},
      Setting{.path = "/sys/kernel/slab/kmalloc-8/failslab", .value = "1"}};
  for (const auto &setting : settings) { write_setting(setting); }
  write_setting({.path = "/sys/kernel/debug/failslab/probability", .value = "100"});
  // No syscall or logging intervenes between task selection and the target ioctl.
  require(write(task, "1", 1) == 1, "Select only the current reader task");
  const int result = ioctl(control, SNDRV_CTL_IOCTL_ELEM_READ, &value);
  const int actual_error = errno;
  require(write(task, "0", 1) == 1, "Clear reader task selection");
  write_setting({.path = "/sys/kernel/debug/failslab/probability", .value = "0"});
  write_setting({.path = "/sys/kernel/slab/kmalloc-8/failslab", .value = "0"});
  checked(close(task), "Close task filter");
  require(result == -1 && actual_error == ENOMEM, "Status propagates allocation failure");
  require(std::ranges::all_of(value.value.bytes.data, [](auto byte) { return byte == sentinel; }),
          "Allocation failure preserves all caller bytes");
  checked(ioctl(control, SNDRV_CTL_IOCTL_ELEM_READ, &value), "Same-handle status recovery");
  constexpr std::array<std::uint8_t, 8> expected{1, 0, 0, 0x80, 0xbb, 0, 0, 0};
  require(std::equal(expected.begin(), expected.end(), value.value.bytes.data),
          "Status lock and PM guard released after allocation failure");
  checked(close(control), "Close allocation-fault control");
  std::puts("OCTA_VM_STATUS_ALLOCATION_PASS");
}

struct PcmSetting {
  unsigned int parameter;
  unsigned int value;
};

void pcm_mask(snd_pcm_hw_params &parameters, PcmSetting setting) {
  auto &mask = parameters.masks[setting.parameter - SNDRV_PCM_HW_PARAM_FIRST_MASK];
  std::ranges::fill(mask.bits, 0U);
  mask.bits[setting.value / 32U] = 1U << (setting.value % 32U);
}

void pcm_interval(snd_pcm_hw_params &parameters, PcmSetting setting) {
  auto &interval = parameters.intervals[setting.parameter - SNDRV_PCM_HW_PARAM_FIRST_INTERVAL];
  interval.min = setting.value;
  interval.max = setting.value;
  interval.integer = 1;
}

snd_pcm_hw_params pcm_parameters(unsigned int channels) {
  snd_pcm_hw_params parameters{};
  constexpr auto maximum = std::numeric_limits<unsigned int>::max();
  for (auto &mask : parameters.masks) { std::ranges::fill(mask.bits, maximum); }
  for (auto &interval : parameters.intervals) { interval.max = maximum; }
  parameters.rmask = maximum;
  pcm_mask(parameters,
           {.parameter = SNDRV_PCM_HW_PARAM_ACCESS, .value = SNDRV_PCM_ACCESS_RW_INTERLEAVED});
  pcm_mask(parameters, {.parameter = SNDRV_PCM_HW_PARAM_FORMAT, .value = SNDRV_PCM_FORMAT_S24_3LE});
  pcm_mask(parameters,
           {.parameter = SNDRV_PCM_HW_PARAM_SUBFORMAT, .value = SNDRV_PCM_SUBFORMAT_STD});
  pcm_interval(parameters, {.parameter = SNDRV_PCM_HW_PARAM_CHANNELS, .value = channels});
  pcm_interval(parameters, {.parameter = SNDRV_PCM_HW_PARAM_RATE, .value = pcm_rate_hz});
  pcm_interval(parameters, {.parameter = SNDRV_PCM_HW_PARAM_PERIOD_SIZE, .value = 256});
  pcm_interval(parameters, {.parameter = SNDRV_PCM_HW_PARAM_PERIODS, .value = 4});
  pcm_interval(parameters, {.parameter = SNDRV_PCM_HW_PARAM_BUFFER_SIZE, .value = 1024});
  return parameters;
}

void reject_pcm() {
  for (const bool capture : {false, true}) {
    const char *const path = capture ? "/dev/snd/pcmC0D0c" : "/dev/snd/pcmC0D0p";
    const int fd = checked(open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC), "Open modeled PCM");
    for (unsigned int iteration = 0; iteration < 2; ++iteration) {
      auto parameters = pcm_parameters(capture ? 12U : 10U);
      checked(ioctl(fd, SNDRV_PCM_IOCTL_HW_REFINE, &parameters), "Refine legal PCM parameters");
      checked(ioctl(fd, SNDRV_PCM_IOCTL_HW_PARAMS, &parameters), "Configure modeled PCM");
      const int result = ioctl(fd, SNDRV_PCM_IOCTL_PREPARE, nullptr);
      const int error = errno;
      std::cout << "PCM_PREPARE " << capture << ' ' << iteration << " result=" << result
                << " errno=" << error << '\n'
                << std::flush;
      require(result == -1 && error == EPIPE, "PCM prepare propagates USB alternate stall");
      checked(ioctl(fd, SNDRV_PCM_IOCTL_HW_FREE, nullptr), "Free failed PCM setup");
    }
    checked(close(fd), "Close failed PCM");
  }
  std::puts("OCTA_VM_PCM_FAILURE_PASS");
}

struct MonEvent {
  std::string id;
  std::string endpoint;
  std::string status;
  char type{};
  unsigned int length{};
};

MonEvent status_event(int monitor) {
  for (;;) {
    std::array<char, 1024> buffer{};
    const auto count = read(monitor, buffer.data(), buffer.size());
    require(count > 0, "USB monitor read");
    std::istringstream input(std::string(buffer.data(), static_cast<std::size_t>(count)));
    MonEvent event;
    unsigned int timestamp = 0;
    require(static_cast<bool>(input >> event.id >> timestamp >> event.type >> event.endpoint),
            "USB monitor event header");
    if (!event.endpoint.starts_with("Ii:1:") || !event.endpoint.ends_with(":2")) { continue; }
    require(static_cast<bool>(input >> event.status >> event.length), "USB status monitor event");
    return event;
  }
}

pid_t process_id(std::string_view text) {
  pid_t result = 0;
  const auto parsed = std::from_chars(text.data(), text.data() + text.size(), result);
  require(parsed.ec == std::errc{} && parsed.ptr == text.data() + text.size() && result > 1,
          "Positive gadget process ID");
  return result;
}

void disconnect_read(pid_t gadget) {
  std::array<int, 2> response{};
  checked(pipe2(response.data(), O_CLOEXEC | O_NONBLOCK), "Unplug result pipe");
  const int monitor =
      checked(open("/sys/kernel/debug/usb/usbmon/1u", O_RDONLY | O_CLOEXEC), "USB monitor open");
  const pid_t parent = getpid();
  const pid_t reader = checked(fork(), "Fork unplug reader");
  if (reader == 0) {
    checked(prctl(PR_SET_PDEATHSIG, SIGTERM), "Reader parent death signal");
    require(getppid() == parent, "Reader parent still alive");
    checked(close(monitor), "Close inherited USB monitor");
    checked(close(response[0]), "Close inherited result reader");
    read_status(scenario("disconnect"), response[1]);
    checked(close(response[1]), "Close child result writer");
    std::cout << std::flush;
    std::_Exit(0);
  }
  checked(close(response[1]), "Close parent result writer");
  // Bound monitor observation; a missing URB is a failure, not a sleep-based pass.
  alarm(3);
  auto event = status_event(monitor);
  require(event.type == 'S' && event.length == 8 &&
              event.status.starts_with(std::to_string(-EINPROGRESS) + ':'),
          "Pending status URB observed before unplug");
  const auto submitted = event.id;
  int actual_error = 0;
  require(read(response[0], &actual_error, sizeof(actual_error)) == -1 && errno == EAGAIN,
          "ALSA read has not returned before unplug");
  int result = 0;
  require(checked(waitpid(reader, &result, WNOHANG), "Reader pending") == 0,
          "Reader still pending before unplug");
  checked(kill(gadget, SIGTERM), "Unplug gadget during pending read");
  event = status_event(monitor);
  std::cout << "UNPLUG_URB " << event.type << ' ' << event.status << ' ' << event.length << '\n'
            << std::flush;
  require(event.type == 'C' && event.id == submitted && event.length == 0,
          "Submitted URB completed with disconnect status");
  const auto separator = event.status.find(':');
  require(separator != std::string::npos, "Interrupt status interval separator");
  int completion = 0;
  const auto parsed =
      std::from_chars(event.status.data(), event.status.data() + separator, completion);
  constexpr std::array disconnect_errors = {-EPROTO, -ESHUTDOWN, -ENODEV};
  require(parsed.ec == std::errc{} && parsed.ptr == event.status.data() + separator &&
              std::ranges::find(disconnect_errors, completion) != disconnect_errors.end(),
          "Disconnect completion is not an ordinary request timeout");
  checked(waitpid(reader, &result, 0), "Reap unplug reader");
  require(WIFEXITED(result) && WEXITSTATUS(result) == 0, "Unplug reader passed");
  require(read(response[0], &actual_error, sizeof(actual_error)) == sizeof(actual_error) &&
              actual_error == -completion,
          "ALSA error matches the observed URB completion");
  alarm(0);
  checked(close(monitor), "Close USB monitor");
  checked(close(response[0]), "Close unplug result reader");
  std::puts("OCTA_VM_STATUS_UNPLUG_PASS");
}
} // namespace

int main(int argc, char **argv) {
  guard();
  require(argc == 2 || argc == 3, "Expected gadget/read-status and optional scenario");
  if (std::string_view(argv[1]) == "disconnect-read") {
    require(argc == 3, "Gadget process ID required");
    disconnect_read(process_id(argv[2]));
    return 0;
  }
  if (std::string_view(argv[1]) == "reject-pcm") {
    require(argc == 2, "PCM check accepts no scenario");
    reject_pcm();
    return 0;
  }
  if (std::string_view(argv[1]) == "allocation-status") {
    require(argc == 2, "Allocation check accepts no scenario");
    allocation_status();
    return 0;
  }
  const auto test = scenario(argc == 3 ? argv[2] : "normal");
  if (std::string_view(argv[1]) == "gadget") {
    Gadget gadget(test);
    gadget.run();
  } else if (std::string_view(argv[1]) == "read-status") {
    read_status(test);
  } else {
    require(false, "Unknown command");
  }
}
