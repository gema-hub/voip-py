import 'dart:convert';
import 'dart:io' show RawDatagramSocket, InternetAddress;
// Eliminado kIsWeb para build solo Android
// import 'package:flutter/foundation.dart' show kIsWeb;

class Datagram {
  final List<int> data;
  Datagram(this.data);
}

abstract class UdpSocket {
  void listen(void Function(Datagram d) onData);
  void close();
  // Nuevo: envío desde el mismo socket persistente para mantener el mapeo NAT
  void send(List<int> data, String host, int port);
}

class MobileUdpSocket implements UdpSocket {
  final RawDatagramSocket _sock;
  MobileUdpSocket(this._sock) {
    _sock.listen((event) {
      final dg = _sock.receive();
      if (dg != null) {
        _onData?.call(Datagram(dg.data));
      }
    });
  }
  void Function(Datagram d)? _onData;
  @override
  void listen(void Function(Datagram d) onData) {
    _onData = onData;
  }
  @override
  void close() {
    _sock.close();
  }
  @override
  void send(List<int> data, String host, int port) {
    _sock.send(data, InternetAddress(host), port);
  }
  static Future<MobileUdpSocket> bind(int port) async {
    final sock = await RawDatagramSocket.bind(InternetAddress.anyIPv4, port);
    return MobileUdpSocket(sock);
  }
}

// Eliminado soporte web: eliminar clase WebUdpSocket
// class WebUdpSocket implements UdpSocket {
//   @override
//   void listen(void Function(Datagram d) onData) {
//     // No-op en web preview, no hay UDP disponible
//   }
//   @override
//   void close() {}
//   @override
//   void send(List<int> data, String host, int port) {
//     // No-op en web
//   }
// }

Future<UdpSocket> UdpSocket_bind(int port) async {
  // Solo Android/iOS
  return await MobileUdpSocket.bind(port);
}

Future<void> sendToServer(String payload, String host, int port) async {
  // Solo Android/iOS (para compatibilidad antigua, se usaba socket efímero)
  final sock = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
  sock.send(utf8.encode(payload), InternetAddress(host), port);
  sock.close();
}
