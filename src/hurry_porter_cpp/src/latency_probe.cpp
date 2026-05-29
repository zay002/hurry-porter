// Copyright 2026 zay002
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"

using namespace std::chrono_literals;

class HurryLatencyProbe final : public rclcpp::Node {
public:
  HurryLatencyProbe()
  : Node("hurry_latency_probe")
  {
    transport_ = declare_parameter<std::string>("transport", "placeholder");
    timer_ = create_wall_timer(1s, [this]() {
          RCLCPP_INFO_ONCE(
          get_logger(),
          "hurry_porter_cpp is ready; future low-latency bridge transport: %s",
          transport_.c_str());
      });
  }

private:
  std::string transport_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<HurryLatencyProbe>());
  rclcpp::shutdown();
  return 0;
}
