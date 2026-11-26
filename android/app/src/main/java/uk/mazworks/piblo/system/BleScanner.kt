package uk.mazworks.piblo.system

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import uk.mazworks.piblo.proto.Sighting
import uk.mazworks.piblo.proto.appleModelId

/**
 * Foreground-only Bluetooth LE scan for Find devices. Runs only while the Find screen is open
 * and the person pressed Scan; never in the background, never for location.
 */
internal class BleScanner(private val context: Context) {
    enum class Status { IDLE, SCANNING, NO_PERMISSION, BLUETOOTH_OFF, UNSUPPORTED, FAILED }

    private val manager = context.getSystemService(BluetoothManager::class.java)
    private val _status = MutableStateFlow(Status.IDLE)
    val status: StateFlow<Status> = _status
    private val lock = Any()
    private val pending = ArrayList<Sighting>()
    /** address -> last RSSIs, newest last (for the hotter/colder meter). */
    private val history = HashMap<String, ArrayDeque<Int>>()
    /** address -> (Apple model id, time) for adverts with rotating addresses. */
    private val appleSeen = HashMap<String, Pair<String, Long>>()
    private val _tick = MutableStateFlow(0L)
    val tick: StateFlow<Long> = _tick

    fun hasPermission(): Boolean = requiredPermissions().all {
        context.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED
    }

    private val callback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) = record(result)
        override fun onBatchScanResults(results: MutableList<ScanResult>) = results.forEach(::record)
        override fun onScanFailed(errorCode: Int) { _status.value = Status.FAILED }
    }

    @SuppressLint("MissingPermission")
    fun start() {
        val adapter = manager?.adapter
        when {
            adapter == null -> { _status.value = Status.UNSUPPORTED; return }
            !hasPermission() -> { _status.value = Status.NO_PERMISSION; return }
            !adapter.isEnabled -> { _status.value = Status.BLUETOOTH_OFF; return }
        }
        val scanner = adapter!!.bluetoothLeScanner ?: run { _status.value = Status.BLUETOOTH_OFF; return }
        bonded = runCatching { adapter.bondedDevices.map { it.address.uppercase() }.toSet() }.getOrDefault(emptySet())
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        try {
            scanner.startScan(null, settings, callback)
            _status.value = Status.SCANNING
        } catch (e: SecurityException) {
            _status.value = Status.NO_PERMISSION
        }
    }

    @SuppressLint("MissingPermission")
    fun stop() {
        runCatching { manager?.adapter?.bluetoothLeScanner?.stopScan(callback) }
        if (_status.value == Status.SCANNING) _status.value = Status.IDLE
    }

    private var bonded: Set<String> = emptySet()

    @SuppressLint("MissingPermission")
    private fun record(r: ScanResult) {
        val address = r.device.address.uppercase()
        val apple = r.scanRecord?.getManufacturerSpecificData(0x004C)
        val name = r.scanRecord?.deviceName ?: runCatching { r.device.name }.getOrNull().orEmpty()
        val tx = r.scanRecord?.txPowerLevel?.takeIf { it != Int.MIN_VALUE && it in -127..20 }
        val s = Sighting(System.currentTimeMillis(), address, name, r.rssi, tx, address in bonded,
                         apple?.joinToString("") { "%02X".format(it) }.orEmpty())
        synchronized(lock) {
            pending.removeAll { it.address == address }   // newest per device per batch
            pending.add(s)
            if (pending.size > 300) pending.removeAt(0)
            val h = history.getOrPut(address) { ArrayDeque() }
            h.addLast(r.rssi)
            while (h.size > 30) h.removeFirst()
            if (s.appleData.isNotEmpty()) appleModelId(s.appleData).takeIf { it.isNotEmpty() }?.let { appleSeen[address] = it to s.atMs }
        }
        _tick.value = System.currentTimeMillis()
    }

    /** Sightings since the last drain, to post to Core's /finddevices/sightings. */
    fun drain(): List<Sighting> = synchronized(lock) { pending.toList().also { pending.clear() } }

    fun rssiHistory(address: String): List<Int> = synchronized(lock) { history[address.uppercase()]?.toList().orEmpty() }

    /** Strongest address heard in the last 10 s for an Apple model (AirPods on an iPhone rotate addresses). */
    fun strongestApple(modelId: String): String? = synchronized(lock) {
        val now = System.currentTimeMillis()
        appleSeen.filter { (_, v) -> (modelId.isEmpty() || v.first == modelId) && now - v.second < 10_000 }
            .keys.maxByOrNull { history[it]?.lastOrNull() ?: -200 }
    }

    companion object {
        fun requiredPermissions(): Array<String> =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
            else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
    }
}
