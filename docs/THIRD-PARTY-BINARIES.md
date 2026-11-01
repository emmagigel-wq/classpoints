# 第三方二进制组件说明

本项目的两个 Windows 二进制组件用途不同，彼此独立，不属于班级积分系统的原创 Python 源码。

## 1. FFmpeg：视频/音频解码与转码

**文件名：** `ffmpeg.exe`  
**项目内预期位置：** `assets/ffmpeg.exe`（打包时）；运行时也会查找用户数据目录下的 `tools/ffmpeg.exe` 和系统 PATH。

班级积分系统通过 FFmpeg 完成媒体处理：
- 解码抽奖/展示等功能使用的视频画面与音频；
- 探测媒体信息、时长和视频流；
- 在需要时把不兼容的视频转为 H.264/AAC MP4；
- 应用也提供单独下载转码组件的功能。

**它不是班级积分系统启动所需的唯一组件。** 源码中的媒体处理模块会查找内置文件、用户数据目录或系统 PATH；找不到时，相关视频播放/转码功能可能不可用，并可提示用户下载组件。

**许可证与分发：** 当前恢复出的二进制版本标识为 `FFmpeg N-92722-gf22fcd4483`，构建参数包含 `--enable-gpl --enable-version3`，因此按 GPLv3 相关构建处理。GPLv3 对二进制再分发有对应源码和构建信息提供义务。由于目前尚未确认该二进制的精确对应源码及构建材料，本仓库不托管、不随源码 ZIP 再分发这份 FFmpeg 二进制。应用源码中的自动下载功能会从其配置的上游发行包地址获取组件；下载到的具体构建仍应按其自身许可证核查。

- FFmpeg 官网： https://ffmpeg.org/
- FFmpeg 法律/许可证说明： https://ffmpeg.org/legal.html
- 项目当前下载配置：`app/services/transcode.py` 中的 `DOWNLOAD_URL`

## 2. cloudflared：Cloudflare 公网隧道客户端

**文件名：** `cloudflared.exe`  
**项目内预期位置：** 与应用打包位置或程序数据目录中的 `cloudflared.exe`；也可从系统 PATH 查找。

班级积分系统调用 Cloudflare 的 `cloudflared` 客户端建立公网隧道，把本机服务端口转发到 HTTPS 公网地址。这样，手机或其他设备即使不在同一 Wi-Fi 网络，也可以通过公网隧道访问班级积分系统提供的连接/同步服务。

它主要服务于**跨网络访问**：
- 不使用公网隧道时，仍可按应用支持的本地网络方式使用；
- 需要跨网络访问时，应用会查找可用的 cloudflared，必要时由用户触发下载；
- 应用负责启动和停止隧道进程，并读取隧道生成的公网地址。

**许可证与分发：** 恢复出的二进制 Go 构建元数据标识 Cloudflare cloudflared，模块版本字符串为 `v0.0.0-20261021105109-de6376c5af4a+dirty`，二进制中包含 Apache License 2.0 许可标识。它不受本项目 MIT 许可证覆盖。公开分发时须遵守其 Apache 2.0 条款并保留适用的版权、许可证及 NOTICE 声明。由于该二进制的版本带有 `+dirty` 标记，不能仅凭模块版本字符串认定它与某个干净上游发行版完全一致。

- Cloudflare cloudflared 源码仓库： https://github.com/cloudflare/cloudflared
- 上游许可证： https://github.com/cloudflare/cloudflared/blob/master/LICENSE
- 上游 Windows x64 最新发行文件： https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
- 项目当前查找、校验与下载逻辑：`app/services/tunnel.py`

## 3. 为什么源码仓库与二进制分开

本仓库的 MIT 许可证只适用于班级积分系统原创项目代码。FFmpeg 和 cloudflared 是独立第三方程序，各自保留原有许可条件。

为避免把不同许可混为一谈：
- 源码 ZIP 不包含这两个大型 EXE；
- 不把第三方 EXE 放进 MIT 授权范围；
- FFmpeg 当前恢复版本在对应源码和构建材料未核实前，不在本仓库公开托管；
- cloudflared 应优先从 Cloudflare 官方发行渠道获取，并在再分发时附带所需许可声明。

二进制文件的用途、版本线索和许可证状态以本说明及 `THIRD-PARTY-NOTICES.md` 为准。实际构建前，请再次核对下载文件的版本、架构和许可证。
