package com.example.phonelisten

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Bundle
import android.view.MotionEvent
import android.animation.AnimatorInflater
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.net.InetSocketAddress
import java.net.Socket
import kotlin.concurrent.thread

class MainActivity : AppCompatActivity() {

    private companion object {
        const val PORT = 8989
        const val SAMPLE_RATE = 16000
        const val PERMISSION_REQ = 100
    }

    private lateinit var etIp: EditText
    private lateinit var tvStatus: TextView
    private lateinit var btnTalk: Button

    @Volatile
    private var streaming = false

    private var streamThread: Thread? = null

    @SuppressLint("ClickableViewAccessibility")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        etIp = findViewById(R.id.etIp)
        tvStatus = findViewById(R.id.tvStatus)
        btnTalk = findViewById(R.id.btnTalk)

        val prefs = getSharedPreferences("config", MODE_PRIVATE)
        etIp.setText(prefs.getString("ip", ""))

        btnTalk.setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    AnimatorInflater.loadAnimator(this, R.animator.btn_press_down).apply {
                        setTarget(btnTalk)
                        start()
                    }
                    if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                        == PackageManager.PERMISSION_GRANTED
                    ) {
                        btnTalk.text = "松开 结束"
                        startTalk()
                    } else {
                        ActivityCompat.requestPermissions(
                            this, arrayOf(Manifest.permission.RECORD_AUDIO), PERMISSION_REQ
                        )
                        setStatus("已申请麦克风权限，请再按一次")
                    }
                    true
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    AnimatorInflater.loadAnimator(this, R.animator.btn_press_up).apply {
                        setTarget(btnTalk)
                        start()
                    }
                    btnTalk.text = "按住 说话"
                    stopTalk()
                    true
                }
                else -> false
            }
        }
    }

    override fun onPause() {
        super.onPause()
        stopTalk()
    }

    private fun startTalk() {
        if (streaming) return
        val ip = etIp.text.toString().trim()
        if (ip.isEmpty()) {
            Toast.makeText(this, "请先填写Windows电脑的IP", Toast.LENGTH_SHORT).show()
            return
        }
        getSharedPreferences("config", MODE_PRIVATE).edit().putString("ip", ip).apply()

        streaming = true
        setStatus("连接 $ip:$PORT ...")
        streamThread = thread(name = "audio-stream") {
            var socket: Socket? = null
            var record: AudioRecord? = null
            try {
                socket = Socket()
                socket.tcpNoDelay = true
                socket.connect(InetSocketAddress(ip, PORT), 3000)
                val out = socket.getOutputStream()

                val minBuf = AudioRecord.getMinBufferSize(
                    SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
                )
                record = AudioRecord(
                    MediaRecorder.AudioSource.MIC, SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                    maxOf(minBuf, 4096)
                )
                if (record.state != AudioRecord.STATE_INITIALIZED) {
                    runOnUiThread {
                        Toast.makeText(this, "麦克风初始化失败", Toast.LENGTH_SHORT).show()
                    }
                    return@thread
                }
                record.startRecording()
                setStatus("正在说话... 松开结束")

                // 每次读 100ms（16000Hz * 2字节），边录边发，电脑端实时出声
                val buf = ByteArray(3200)
                while (streaming) {
                    val n = record.read(buf, 0, buf.size)
                    if (n <= 0) break
                    out.write(buf, 0, n)
                    out.flush()
                }
            } catch (e: Exception) {
                if (streaming) {
                    runOnUiThread {
                        Toast.makeText(this, "发送失败: ${e.message}", Toast.LENGTH_SHORT).show()
                        tvStatus.text = "发送失败，请检查IP和电脑防火墙"
                    }
                }
            } finally {
                streaming = false
                try { record?.stop() } catch (_: Exception) {}
                try { record?.release() } catch (_: Exception) {}
                try { socket?.close() } catch (_: Exception) {}
                setStatus("已停止，按住按钮开始说话")
            }
        }
    }

    private fun stopTalk() {
        streaming = false
        streamThread = null
    }

    private fun setStatus(text: String) {
        runOnUiThread { tvStatus.text = text }
    }
}
