package dev.mgade.tablink

import android.Manifest
import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Point
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.view.Gravity
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Fullscreen view of the desktop. The connection itself lives in
 * [TabLinkService], so switching apps only pauses the video.
 */
class MainActivity : Activity(), SurfaceHolder.Callback {
    private lateinit var statusScreen: View  // black status screen over the video
    private lateinit var status: TextView
    private var service: TabLinkService? = null
    private var holder: SurfaceHolder? = null  // set while the surface exists
    private var reconnectRequested = false     // launched with EXTRA_RECONNECT (e.g. re-plugged)

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            val s = (binder as TabLinkService.LocalBinder).service
            service = s
            s.statusListener = ::showStatus  // replays the current status, so set `service` first
            attachIfReady()
            consumeReconnectRequest()
        }

        override fun onServiceDisconnected(name: ComponentName) {
            service = null
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            window.attributes.layoutInDisplayCutoutMode =
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }

        val surfaceView = SurfaceView(this)
        surfaceView.holder.addCallback(this)
        // The desktop is a touch screen now: every touch goes to the host.
        surfaceView.setOnTouchListener { view, event ->
            service?.touch(event, view.width, view.height)
            true
        }
        val dp = resources.displayMetrics.density
        status = TextView(this).apply {
            setTextColor(Color.WHITE)
            textSize = 20f
            gravity = Gravity.CENTER
        }
        // App icon with the status text right below it, centred on a black screen.
        statusScreen = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            // After the desktop stops the session the text reads "Tap to reconnect".
            setOnClickListener { service?.reconnect() }
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER_HORIZONTAL
                addView(ImageView(context).apply { setImageResource(R.mipmap.ic_launcher) },
                    LinearLayout.LayoutParams((96 * dp).toInt(), (96 * dp).toInt()).apply {
                        bottomMargin = (24 * dp).toInt()
                    })
                // Own width: the default (match parent) would wrap the text to the icon's width.
                addView(status, LinearLayout.LayoutParams(-2, -2))
            }, FrameLayout.LayoutParams(-2, -2, Gravity.CENTER))
        }
        setContentView(FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(surfaceView, FrameLayout.LayoutParams(-1, -1))
            addView(statusScreen, FrameLayout.LayoutParams(-1, -1))
        })

        // Without it the service's notification (and its Disconnect button) is hidden.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 0)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        consumeReconnectRequest()
    }

    /** The desktop launches us with EXTRA_RECONNECT when the tablet is plugged in. */
    private fun consumeReconnectRequest() {
        if (intent.getBooleanExtra(TabLinkService.EXTRA_RECONNECT, false)) {
            intent.removeExtra(TabLinkService.EXTRA_RECONNECT)
            reconnectRequested = true
        }
        val s = service ?: return
        if (reconnectRequested) {
            reconnectRequested = false
            s.reconnect()
        }
    }

    override fun onStart() {
        super.onStart()
        val intent = Intent(this, TabLinkService::class.java)
        startForegroundService(intent)
        bindService(intent, serviceConnection, Context.BIND_AUTO_CREATE)
    }

    override fun onStop() {
        // Android may destroy the surface after this point, once we've unbound.
        service?.detach()
        service?.statusListener = null
        service = null
        unbindService(serviceConnection)
        super.onStop()
    }

    private fun showStatus(text: String?) {
        status.text = text ?: ""
        statusScreen.visibility = if (text == null) View.GONE else View.VISIBLE
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    @Suppress("DEPRECATION")
    private fun hideSystemBars() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.insetsController?.let {
                it.hide(WindowInsets.Type.systemBars())
                it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
        }
    }

    /** Full physical resolution in the current orientation. */
    @Suppress("DEPRECATION")
    private fun screenSize(): Point =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val b = windowManager.maximumWindowMetrics.bounds
            Point(b.width(), b.height())
        } else {
            Point().also { windowManager.defaultDisplay.getRealSize(it) }
        }

    override fun surfaceCreated(holder: SurfaceHolder) {}

    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
        this.holder = holder
        attachIfReady()
    }

    override fun surfaceDestroyed(holder: SurfaceHolder) {
        // Must stop rendering before returning: the surface is gone afterwards.
        this.holder = null
        service?.detach()
    }

    /** Hand the surface to the service once both exist (either can come first). */
    private fun attachIfReady() {
        val h = holder ?: return
        val s = service ?: return
        val size = screenSize()
        if (size.x < size.y) return  // wait for landscape before announcing the size
        s.attach(h.surface, size.x, size.y, resources.displayMetrics.densityDpi)
    }
}
