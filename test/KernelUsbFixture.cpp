// SPDX-License-Identifier: GPL-2.0-only
// Deterministic synthetic USB model, restricted to the private sanitizer VM.
#include <algorithm>
#include <array>
#include <cerrno>
#include <csignal>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <initializer_list>
#include <iostream>
#include <linux/prctl.h>
#include <linux/usb/ch9.h>
#include <linux/usb/raw_gadget.h>
#include <sound/asound.h>
#include <span>
#include <string_view>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/types.h>
#include <sys/utsname.h>
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

Bytes descriptor(std::uint16_t value) {
  switch (value >> 8) {
    case USB_DT_DEVICE: {
      constexpr std::array<std::uint8_t, 18> device{
          18, 1, 0, 2, 0, 0, 0, 64, 0x82, 5, 0x20, 1, 0, 2, 1, 2, 3, 1};
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
  Gadget() : fd_(checked(open("/dev/raw-gadget", O_RDWR | O_CLOEXEC), "raw gadget")) {}

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
    Io io{};
    io.ep = static_cast<std::uint16_t>(status_ep_);
    io.length = 8;
    io.data[3] = 0x80;
    io.data[4] = status_rate_high;
    for (;;) {
      require(checked(ioctl(fd_, USB_RAW_IOCTL_EP_WRITE, &io), "STATUS WRITE") == 8,
              "Full status packet");
      io.data[0] ^= 1;
    }
  }

  void set_interface(const usb_ctrlrequest &request) {
    const bool idle_audio = request.wIndex < 2 && request.wValue == 0;
    const bool midi = request.wIndex == 2 && request.wValue <= 1;
    const bool status_interface = request.wIndex == 3 && request.wValue <= 1;
    const bool auxiliary = request.wIndex == 4 && request.wValue == 0;
    if (!(idle_audio || midi || status_interface || auxiliary)) {
      checked(ioctl(fd_, USB_RAW_IOCTL_EP0_STALL, 0UL), "Unsupported alternate");
      return;
    }
    if (status_interface) { status(request.wValue == 1); }
    alts_[request.wIndex] = static_cast<std::uint8_t>(request.wValue);
    ack();
  }

  void control(const usb_ctrlrequest &request) {
    if (request.bRequestType == 0x80 && request.bRequest == USB_REQ_GET_DESCRIPTOR) {
      const auto bytes = descriptor(request.wValue);
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

void read_status() {
  const int fd = checked(open("/dev/snd/controlC0", O_RDWR | O_CLOEXEC), "ALSA control");
  snd_ctl_card_info card{};
  checked(ioctl(fd, SNDRV_CTL_IOCTL_CARD_INFO, &card), "Card INFO");
  require(std::string_view(reinterpret_cast<const char *>(card.longname)).contains("dummy_hcd"),
          "Virtual card identity");
  snd_ctl_elem_info info{};
  info.id.iface = SNDRV_CTL_ELEM_IFACE_CARD;
  constexpr std::string_view name = "OCTA-CAPTURE Status";
  std::memcpy(info.id.name, name.data(), name.size());
  checked(ioctl(fd, SNDRV_CTL_IOCTL_ELEM_INFO, &info), "Status INFO");
  require(info.type == SNDRV_CTL_ELEM_TYPE_BYTES && info.count == 8, "Status metadata");
  require(info.access == (SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_VOLATILE),
          "Read-only volatile status");
  constexpr int read_count = 20;
  for (int index = 0; index < read_count; ++index) {
    snd_ctl_elem_value value{};
    value.id = info.id;
    checked(ioctl(fd, SNDRV_CTL_IOCTL_ELEM_READ, &value), "Status READ");
    constexpr std::array<std::uint8_t, 8> expected{1, 0, 0, 0x80, 0xbb, 0, 0, 0};
    require(std::equal(expected.begin(), expected.end(), value.value.bytes.data), "Status bytes");
  }
  checked(close(fd), "close ALSA");
  std::puts("OCTA_VM_STATUS_READ_PASS");
}
} // namespace

int main(int argc, char **argv) {
  guard();
  require(argc == 2, "Expected gadget or read-status");
  if (std::string_view(argv[1]) == "gadget") {
    Gadget gadget;
    gadget.run();
  } else if (std::string_view(argv[1]) == "read-status") {
    read_status();
  } else {
    require(false, "Unknown command");
  }
}
