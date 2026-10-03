package dev.mgade.tablink

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.Surface

/**
 * Foreground service that owns the [Connection], so the virtual monitor on the
 * desktop survives the app going to the background. MainActivity binds to it
 * and attaches its Surface while visible.
 */
class TabLinkService : Service() {
    inner class LocalBinder : Binder() {
        val service get() = this@TabLinkService
    }

    private val binder = LocalBinder()
    private val handler = Handler(Looper.getMainLooper())
    private var connection: Connection? = null
    private var attached = false

    /** Status for the activity's overlay (null: video is showing). Main thread. */
    var statusListener: ((String?) -> Unit)? = null
        set(value) {
            field = value
            value?.invoke(lastStatus)
        }
    private var lastStatus: String? = "Starting…"

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, getString(R.string.app_name), NotificationManager.IMPORTANCE_LOW))
        val notification = notification("Starting…")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        handler.postDelayed(::stopIfIdle, IDLE_TIMEOUT_MS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_DISCONNECT -> stopSelf()
            ACTION_RECONNECT -> reconnect()
        }
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent): IBinder = binder

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        connection?.stop()
        connection = null
        super.onDestroy()
    }

    private fun token(): String =
        getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_TOKEN, "") ?: ""

    /** The activity is showing [surface]; (re)connect if the screen size changed. */
    fun attach(surface: Surface, width: Int, height: Int, dpi: Int) {
        attached = true
        val c = connection
        if (c == null || c.width != width || c.height != height) {
            c?.stop()
            connection = Connection(width, height, dpi, ::token, ::onStatus).also { it.start() }
        }
        connection!!.attach(surface)
    }

    /** Reconnect after the desktop stopped the session. No-op otherwise. */
    fun reconnect() {
        connection?.resume()
    }

    /** Forward a touch on the activity's view (main thread). */
    fun touch(event: android.view.MotionEvent, viewWidth: Int, viewHeight: Int) {
        connection?.sendTouch(event, viewWidth, viewHeight)
    }

    /** The activity went to the background. The connection keeps running. */
    fun detach() {
        attached = false
        connection?.detach()
    }

    private fun onStatus(text: String?) = handler.post {
        lastStatus = text
        statusListener?.invoke(text)
        val summary = when {
            connection?.stoppedByDesktop == true -> STOPPED_SUMMARY
            connection?.connected != true -> "Waiting for the desktop"
            attached -> "Showing the desktop"
            else -> "Connected (paused while in the background)"
        }
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(summary))
    }

    /** Stop when nothing needs us: in the background and no desktop connected. */
    private fun stopIfIdle() {
        if (!attached && connection?.connected != true) {
            stopSelf()
        } else {
            handler.postDelayed(::stopIfIdle, IDLE_TIMEOUT_MS)
        }
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val disconnect = PendingIntent.getService(this, 0,
            Intent(this, TabLinkService::class.java).setAction(ACTION_DISCONNECT), PendingIntent.FLAG_IMMUTABLE)
        val builder = Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
        if (text == STOPPED_SUMMARY) {
            val reconnect = PendingIntent.getService(this, 1,
                Intent(this, TabLinkService::class.java).setAction(ACTION_RECONNECT), PendingIntent.FLAG_IMMUTABLE)
            builder.addAction(Notification.Action.Builder(null, "Reconnect", reconnect).build())
        }
        return builder
            .addAction(Notification.Action.Builder(null, "Disconnect", disconnect).build())
            .build()
    }

    companion object {
        private const val CHANNEL = "tablink"
        private const val NOTIFICATION_ID = 1
        private const val ACTION_DISCONNECT = "dev.mgade.tablink.DISCONNECT"
        private const val ACTION_RECONNECT = "dev.mgade.tablink.RECONNECT"
        private const val STOPPED_SUMMARY = "Stopped from the desktop"
        /** Intent extra for MainActivity: reconnect even if the desktop stopped the session. */
        const val EXTRA_RECONNECT = "dev.mgade.tablink.extra.RECONNECT"
        /** Intent extra for MainActivity: the host's session token (host/scripts/lib.sh). */
        const val EXTRA_TOKEN = "dev.mgade.tablink.extra.TOKEN"
        /** Private app storage for the token, so a relaunch from the launcher still connects. */
        const val PREFS = "tablink"
        const val KEY_TOKEN = "token"
        private const val IDLE_TIMEOUT_MS = 60_000L
    }
}
