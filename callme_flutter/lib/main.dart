import 'dart:async';
import 'dart:convert';
import 'dart:io' show InternetAddress, InternetAddressType;

import 'package:flutter/material.dart';
// Eliminado kIsWeb para build solo Android
// import 'package:flutter/foundation.dart' show kIsWeb;
import 'signaling.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';

void main() {
  runApp(const VoipApp());
}

class VoipApp extends StatelessWidget {
  const VoipApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'VoIP Pro',
      theme: ThemeData.dark().copyWith(
        colorScheme: const ColorScheme.dark(primary: Color(0xFF00FF88)),
      ),
      home: const ModernPhone(),
    );
  }
}

class ModernPhone extends StatefulWidget {
  const ModernPhone({super.key});

  @override
  State<ModernPhone> createState() => _ModernPhoneState();
}

class _ModernPhoneState extends State<ModernPhone> {
  final TextEditingController _display = TextEditingController();
  String _status = 'Listo';
  bool _connected = false;
  String? _myNumber;
  String? _myName;
  String? _peerNumber;
  // Modo servidor: sin P2P
  // String? _peerIp;
  // int? _peerPort;
  int _localPort = 0;
  String _relayHost = 'jacob.hidencloud.com';
  int _relayPort = 24646;

  UdpSocket? _udp;
  Timer? _pingTimer;
  Timer? _listTimer;

  // WebRTC audio
  RTCPeerConnection? _pc;
  MediaStream? _localStream;
  final RTCVideoRenderer _remoteRenderer = RTCVideoRenderer();
  bool _muted = false;
  bool _speakerOn = false;
  int _onlineCount = 0;
  Timer? _callTimer;
  int _callSeconds = 0;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    _localPort = 50000 + (DateTime.now().millisecondsSinceEpoch % 10000);
    await _remoteRenderer.initialize();
    // Modo servidor: sin renderers de video
    // await _localRenderer.initialize();
    // await _remoteRenderer.initialize();

    _myNumber = await _promptNumber();
    if (_myNumber == null || _myNumber!.isEmpty) {
      return;
    }
    _myName = await _promptName();
    if (_myName == null) _myName = '';
    await _promptRelay();

    _udp = await UdpSocket_bind(_localPort);
    _listen();
    _register();
    _pingTimer = Timer.periodic(const Duration(seconds: 10), (_) => _ping());
    _listTimer = Timer.periodic(const Duration(seconds: 20), (_) => _requestList());
  }

  Future<String?> _promptNumber() async {
    return await showDialog<String>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) {
        final c = TextEditingController();
        return AlertDialog(
          title: const Text('Ingresa tu número único'),
          content: TextField(controller: c),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(c.text.trim()),
              child: const Text('OK'),
            )
          ],
        );
      },
    );
  }

  Future<String?> _promptName() async {
    return await showDialog<String>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) {
        final c = TextEditingController();
        return AlertDialog(
          title: const Text('Ingresa tu nombre'),
          content: TextField(controller: c),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(c.text.trim()),
              child: const Text('OK'),
            )
          ],
        );
      },
    );
  }
  Future<void> _promptRelay() async {
    final hostController = TextEditingController(text: _relayHost);
    final portController = TextEditingController(text: _relayPort.toString());
    final res = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) {
        return AlertDialog(
          title: const Text('Servidor de señalización'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: hostController,
                decoration: const InputDecoration(labelText: 'Host/IP'),
              ),
              TextField(
                controller: portController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Puerto'),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('OK'),
            ),
          ],
        );
      },
    );
    if (res == true) {
      final h = hostController.text.trim();
      final p = int.tryParse(portController.text.trim());
      setState(() {
        if (h.isNotEmpty) _relayHost = h;
        if (p != null && p > 0) _relayPort = p;
      });
    }
  }
  Future<void> _changeServer() async {
    await _promptRelay();
    _onlineCount = 0;
    _register();
  }

  void _listen() {
    _udp?.listen((datagram) async {
      final data = datagram.data;
      String? text;
      try {
        text = utf8.decode(data, allowMalformed: true);
      } catch (_) {}

      if (_connected) {
        if (text != null && text.startsWith('BYE_FROM:')) {
          // Recibido fin de llamada: colgar
          _hangupInternal();
          return;
        }
      }

      if (text == null) return;

      // Modo servidor: solo ACKs/latidos/listados
      if (text == 'OK') {
        setState(() => _status = 'Conectado');
      } else if (text == 'PONG') {
        if (_status == 'Sin conexión') {
          setState(() => _status = 'Conectado');
        }
      } else if (text.startsWith('LIST:')) {
        final payload = text.substring('LIST:'.length);
        final items = payload.isEmpty ? <String>[] : payload.split(',');
        setState(() {
          _status = 'Conectado';
          _onlineCount = items.where((e) => e.trim().isNotEmpty).length;
        });
      } else if (text.startsWith('OFFER_FROM_B64:')) {
        try {
          final parts = text.split(':');
          final from = parts[1];
          final b64 = parts[2];
          final jsonStr = utf8.decode(base64Decode(b64));
          final obj = jsonDecode(jsonStr);
          final sdp = obj['sdp'];
          final type = obj['type'];
          _peerNumber = from;
          if (_pc == null) {
            await _preparePeerConnection();
          }
          await _pc!.setRemoteDescription(RTCSessionDescription(sdp, type));
          final answer = await _pc!.createAnswer({'offerToReceiveAudio': 1});
          await _pc!.setLocalDescription(answer);
          final aB64 = base64Encode(utf8.encode(jsonEncode({'sdp': answer.sdp, 'type': answer.type})));
          await _sendServer('ANSWER_B64:${from}:${_myNumber}:$aB64');
          setState(() {
            _connected = true;
            _status = 'En llamada con $from';
          });
          _startCallTimer();
        } catch (_) {}
      } else if (text.startsWith('ANSWER_FROM_B64:')) {
        try {
          final parts = text.split(':');
          final from = parts[1];
          final b64 = parts[2];
          final jsonStr = utf8.decode(base64Decode(b64));
          final obj = jsonDecode(jsonStr);
          final sdp = obj['sdp'];
          final type = obj['type'];
          await _pc?.setRemoteDescription(RTCSessionDescription(sdp, type));
          setState(() {
            _connected = true;
            _status = 'En llamada con $from';
          });
          _startCallTimer();
        } catch (_) {}
      } else if (text.startsWith('ICE_FROM_B64:')) {
        try {
          final parts = text.split(':');
          final from = parts[1];
          final b64 = parts[2];
          final jsonStr = utf8.decode(base64Decode(b64));
          final obj = jsonDecode(jsonStr);
          final cand = RTCIceCandidate(obj['candidate'], obj['sdpMid'], obj['sdpMLineIndex']);
          await _pc?.addCandidate(cand);
        } catch (_) {}
      } else if (text.startsWith('CALL_FROM:')) {
        final parts = text.split(':');
        final caller = parts.length >= 2 ? parts[1] : '';
        final callerName = parts.length >= 3 ? parts[2] : '';
        if (_connected == true) {
          if (caller.isNotEmpty && _myNumber != null) {
            _udp?.send(utf8.encode('BUSY:${caller}:${_myNumber}'), _relayHost, _relayPort);
          }
        } else {
          _showIncomingDialog(callerName: callerName, callerNumber: caller);
        }
      } else if (text.startsWith('ACCEPT_FROM:')) {
        final parts = text.split(':');
        final callee = parts.length >= 2 ? parts[1] : '';
        if (_peerNumber == callee && _pc == null) {
          await _startWebRTC(isCaller: true);
        }
      } else if (text.startsWith('REJECT_FROM:')) {
        setState(() => _status = 'Rechazada');
        Future.delayed(const Duration(seconds: 2), _resetCall);
      } else if (text.startsWith('BUSY_FROM:')) {
        setState(() => _status = 'Ocupado');
        Future.delayed(const Duration(seconds: 2), _resetCall);
      } else if (text.startsWith('BYE_FROM:')) {
        _hangupInternal();
      }
      // Bloques P2P desactivados
      // if (text.startsWith('RING_FROM:')) { ... }
      // else if (text.startsWith('PEER_FROM:')) { ... }
      // else if (text.startsWith('PEER:')) { ... }
      // else if (text.startsWith('OFFER_FROM:')) { ... }
      // else if (text.startsWith('ANSWER_FROM:')) { ... }
      // else if (text.startsWith('ICE_FROM:')) { ... }
    });
  }

  Future<void> _register() async {
    setState(() => _status = 'Conectando...');
    for (int i = 0; i < 10; i++) {
      try {
        await _sendServer('REGISTER:${_myNumber}:${_localPort}:${_myName ?? ''}');
      } catch (_) {
        await Future.delayed(const Duration(seconds: 3));
      }
      await Future.delayed(const Duration(milliseconds: 500));
    }
    setState(() => _status = 'Sin conexión');
  }

  Future<void> _ping() async {
    try {
      await _sendServer('PING:${_myNumber}');
    } catch (_) {}
  }

  Future<void> _call() async {
    final callee = _display.text.trim();
    if (callee.isEmpty || _connected == true) return;

    setState(() {
      _peerNumber = callee;
      _status = 'Llamando a $callee...';
    });

    try {
      await _sendServer('CALL:$callee:${_myNumber}');
    } catch (_) {
      setState(() => _status = 'Sin respuesta');
      Future.delayed(const Duration(seconds: 3), _resetCall);
    }
  }

  void _showIncomingDialog({required String callerName, required String callerNumber}) async {
    if (callerNumber.isEmpty) return;
    setState(() {
      _peerNumber = callerNumber;
      _status = 'Llamada entrante de $callerNumber';
    });
    final res = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) {
        return AlertDialog(
          title: const Text('Llamada entrante'),
          content: Text('De: ${callerName.isNotEmpty ? callerName : callerNumber}'),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Rechazar'),
            ),
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('Aceptar'),
            ),
          ],
        );
      },
    );
    if (res == true) {
      if (_myNumber != null) {
        await _sendServer('ACCEPT:${callerNumber}:${_myNumber}');
        setState(() => _status = 'Conectando...');
      }
    } else {
      if (_myNumber != null) {
        await _sendServer('REJECT:${callerNumber}:${_myNumber}');
        _resetCall();
      }
    }
  }

  Future<void> _startWebRTC({required bool isCaller}) async {
    try {
      await _preparePeerConnection();
      if (isCaller) {
        final offer = await _pc!.createOffer({'offerToReceiveAudio': 1});
        await _pc!.setLocalDescription(offer);
        final oB64 = base64Encode(utf8.encode(jsonEncode({'sdp': offer.sdp, 'type': offer.type})));
        if (_peerNumber != null) {
          await _sendServer('OFFER_B64:${_peerNumber}:${_myNumber}:$oB64');
        }
      }
    } catch (e) {
      setState(() => _status = 'Error WebRTC');
    }
  }

  Future<void> _preparePeerConnection() async {
    final config = {
      'iceServers': [
        {'urls': ['stun:stun.l.google.com:19302']}
      ]
    };
    _pc = await createPeerConnection(config);
    _pc!.onAddStream = (MediaStream s) {
      _remoteRenderer.srcObject = s;
      setState(() {
        _connected = true;
      });
    };
    _pc!.onIceCandidate = (RTCIceCandidate cand) {
      try {
        final candJson = jsonEncode({'candidate': cand.candidate, 'sdpMid': cand.sdpMid, 'sdpMLineIndex': cand.sdpMLineIndex});
        final cB64 = base64Encode(utf8.encode(candJson));
        if (_peerNumber != null) {
          _sendServer('ICE_B64:${_peerNumber}:${_myNumber}:$cB64');
        }
      } catch (_) {}
    };
    _localStream = await navigator.mediaDevices.getUserMedia({
      'audio': {
        'echoCancellation': true,
        'noiseSuppression': true,
        'autoGainControl': true,
      },
      'video': false
    });
    await _pc!.addStream(_localStream!);
  }

  void _hangupInternal() {
    setState(() {
      _connected = false;
      _status = 'Listo';
    });
    _stopCallTimer();
    _peerNumber = null;
    // _peerIp = null;
    // _peerPort = null;
    _pc?.close();
    _pc = null;
    _localStream?.dispose();
    _localStream = null;
    _remoteRenderer.srcObject = null;
    // _localRenderer?.dispose();
  }

  void _resetCall() {
    setState(() {
      _status = 'Listo';
    });
    _peerNumber = null;
  }

  // Helpers de marcado: mover a nivel de clase
  void _appendDigit(String d) {
    if (_connected == true) return;
    final t = _display.text;
    _display.text = t + d;
    setState(() {});
  }

  void _deleteDigit() {
    if (_connected == true) return;
    final t = _display.text;
    if (t.isNotEmpty) {
      _display.text = t.substring(0, t.length - 1);
      setState(() {});
    }
  }

  Future<void> _requestList() async {
    try {
      await _sendServer('LIST');
    } catch (_) {}
  }

  @override
  void dispose() {
    // Intentar darse de baja del servidor para limpiar registro
    _pingTimer?.cancel();
    _listTimer?.cancel();
    try {
      if (_myNumber != null && _myNumber!.isNotEmpty) {
        _sendServer('UNREGISTER:${_myNumber}');
      }
    } catch (_) {}
    _udp?.close();
    _pc?.close();
    _localStream?.dispose();
    _remoteRenderer.dispose();
    // Modo servidor: sin renderers de video
    // _localRenderer.dispose();
    // _remoteRenderer.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0A0A0F),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24.0, vertical: 32.0),
          child: Column(
            children: [
              Text('${_myNumber ?? ''}${(_myName != null && _myName!.isNotEmpty) ? ' - ' + _myName! : ''}', textAlign: TextAlign.center, style: const TextStyle(color: Color(0xFF00FF88), fontSize: 32, fontWeight: FontWeight.bold)),
              const SizedBox(height: 24),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text('Servidor: ${_relayHost}:${_relayPort}', style: const TextStyle(color: Colors.white70, fontSize: 14)),
                  const SizedBox(width: 8),
                  IconButton(
                    icon: const Icon(Icons.settings, color: Color(0xFF00FF88)),
                    onPressed: _changeServer,
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text('Estado: ${_status}', style: const TextStyle(color: Color(0xFF00FF88), fontSize: 16)),
                  const SizedBox(width: 16),
                  Text('Conectados: ${_onlineCount}', style: const TextStyle(color: Colors.white70, fontSize: 16)),
                ],
              ),
              const SizedBox(height: 12),
              // Sustituir helpers locales por UI de panel numérico + campo de número
              TextField(
                controller: _display,
                readOnly: true,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Color(0xFF00FF88), fontSize: 32, fontWeight: FontWeight.bold),
                decoration: const InputDecoration(
                  hintText: 'Número a llamar',
                  hintStyle: TextStyle(color: Colors.white54),
                  enabledBorder: OutlineInputBorder(borderSide: BorderSide(color: Color(0xFF333333), width: 3), borderRadius: BorderRadius.all(Radius.circular(24))),
                  focusedBorder: OutlineInputBorder(borderSide: BorderSide(color: Color(0xFF00FF88), width: 3), borderRadius: BorderRadius.all(Radius.circular(24))),
                ),
              ),
              const SizedBox(height: 12),
              Column(
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      for (final d in ['1','2','3'])
                        Padding(
                          padding: const EdgeInsets.all(6.0),
                          child: ElevatedButton(
                            onPressed: _connected == true ? null : () => _appendDigit(d),
                            child: Text(d, style: const TextStyle(fontSize: 24)),
                            style: ElevatedButton.styleFrom(minimumSize: const Size(64, 64)),
                          ),
                        ),
                    ],
                  ),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      for (final d in ['4','5','6'])
                        Padding(
                          padding: const EdgeInsets.all(6.0),
                          child: ElevatedButton(
                            onPressed: _connected == true ? null : () => _appendDigit(d),
                            child: Text(d, style: const TextStyle(fontSize: 24)),
                            style: ElevatedButton.styleFrom(minimumSize: const Size(64, 64)),
                          ),
                        ),
                    ],
                  ),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      for (final d in ['7','8','9'])
                        Padding(
                          padding: const EdgeInsets.all(6.0),
                          child: ElevatedButton(
                            onPressed: _connected == true ? null : () => _appendDigit(d),
                            child: Text(d, style: const TextStyle(fontSize: 24)),
                            style: ElevatedButton.styleFrom(minimumSize: const Size(64, 64)),
                          ),
                        ),
                    ],
                  ),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Padding(
                        padding: const EdgeInsets.all(6.0),
                        child: ElevatedButton(
                          onPressed: _connected == true ? null : () => _appendDigit('*'),
                          child: const Text('*', style: TextStyle(fontSize: 24)),
                          style: ElevatedButton.styleFrom(minimumSize: const Size(64, 64)),
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.all(6.0),
                        child: ElevatedButton(
                          onPressed: _connected == true ? null : () => _appendDigit('0'),
                          child: const Text('0', style: TextStyle(fontSize: 24)),
                          style: ElevatedButton.styleFrom(minimumSize: const Size(64, 64)),
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.all(6.0),
                        child: ElevatedButton(
                          onPressed: _connected == true ? null : () => _appendDigit('#'),
                          child: const Text('#', style: TextStyle(fontSize: 24)),
                          style: ElevatedButton.styleFrom(minimumSize: const Size(64, 64)),
                        ),
                      ),
                    ],
                  ),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Padding(
                        padding: const EdgeInsets.all(6.0),
                        child: ElevatedButton(
                          onPressed: _connected == true ? null : _deleteDigit,
                          child: const Icon(Icons.backspace),
                          style: ElevatedButton.styleFrom(minimumSize: const Size(64, 48)),
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.all(6.0),
                        child: ElevatedButton(
                          onPressed: _connected == true ? null : () { _display.clear(); setState((){}); },
                          child: const Text('CLR'),
                          style: ElevatedButton.styleFrom(minimumSize: const Size(64, 48)),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
              const SizedBox(height: 24),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF00FF88), foregroundColor: Colors.black, padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16)),
                    onPressed: _call,
                    child: const Text('LLAMAR', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
                  ),
                  const SizedBox(width: 16),
                  ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFFFF3366), foregroundColor: Colors.white, padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16)),
                    onPressed: _hangup,
                    child: const Text('COLGAR', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  ElevatedButton(
                    onPressed: _toggleMute,
                    style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12)),
                    child: Text(_muted ? 'Mic OFF' : 'Mic ON'),
                  ),
                  const SizedBox(width: 12),
                  ElevatedButton(
                    onPressed: _toggleSpeaker,
                    style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12)),
                    child: Text(_speakerOn ? 'Altavoz ON' : 'Altavoz OFF'),
                  ),
                  const SizedBox(width: 12),
                  Text(_formatDuration(_callSeconds), style: const TextStyle(color: Colors.white70, fontSize: 16)),
                ],
              ),
              const SizedBox(height: 24),
              // Modo servidor: sin video renderers
              // Expanded(
              //   child: Row(
              //     children: [
              //       Expanded(child: RTCVideoView(_localRenderer)),
              //       Expanded(child: RTCVideoView(_remoteRenderer)),
              //     ],
              //   ),
              // )
              // En modo servidor, no mostramos vistas de video
            ],
          ),
        ),
      ),
    );
  }

  void _hangup() {
    try {
      if (_peerNumber != null && _myNumber != null) {
        _sendServer('BYE:${_peerNumber}:${_myNumber}');
      }
    } catch (_) {}
    _hangupInternal();
  }

  Future<InternetAddress> _resolveIPv4(String host) async {
    final parsed = InternetAddress.tryParse(host);
    if (parsed != null && parsed.type == InternetAddressType.IPv4) {
      return parsed;
    }
    final addrs = await InternetAddress.lookup(host);
    for (final a in addrs) {
      if (a.type == InternetAddressType.IPv4) {
        return a;
      }
    }
    throw Exception('IPv4 no encontrado');
  }

  Future<void> _sendServer(String payload) async {
    final ip = await _resolveIPv4(_relayHost);
    _udp?.send(utf8.encode(payload), ip.address, _relayPort);
  }

  void _toggleMute() {
    _muted = !_muted;
    final tracks = _localStream?.getAudioTracks();
    if (tracks != null && tracks.isNotEmpty) {
      for (final t in tracks) {
        t.enabled = !_muted;
      }
    }
    setState(() {});
  }

  void _toggleSpeaker() {
    _speakerOn = !_speakerOn;
    try {
      Helper.setSpeakerphoneOn(_speakerOn);
    } catch (_) {}
    setState(() {});
  }

  void _startCallTimer() {
    _callTimer?.cancel();
    _callSeconds = 0;
    _callTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      setState(() {
        _callSeconds++;
      });
    });
  }

  void _stopCallTimer() {
    _callTimer?.cancel();
    _callTimer = null;
    _callSeconds = 0;
  }

  String _formatDuration(int s) {
    final m = s ~/ 60;
    final ss = s % 60;
    final mm = m.toString().padLeft(2, '0');
    final sss = ss.toString().padLeft(2, '0');
    return '$mm:$sss';
  }
}
