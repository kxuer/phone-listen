FROM gradle:8.2-jdk17

USER root
ENV ANDROID_SDK_ROOT=/opt/android-sdk
ENV ANDROID_HOME=/opt/android-sdk
ENV PATH=$PATH:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/platform-tools

# 安装 Android 命令行工具
RUN mkdir -p /opt/android-sdk/cmdline-tools && \
    cd /opt/android-sdk/cmdline-tools && \
    apt-get update -qq && apt-get install -y -qq unzip wget > /dev/null 2>&1 && \
    wget -q https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip -O cmdline-tools.zip && \
    unzip -q cmdline-tools.zip && \
    mv cmdline-tools latest && \
    rm cmdline-tools.zip

# 接受许可 + 安装 SDK 组件
RUN yes | sdkmanager --licenses > /dev/null
RUN sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0" > /dev/null

WORKDIR /project
