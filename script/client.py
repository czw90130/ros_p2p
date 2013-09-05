#! /usr/bin/env python

import roslib; roslib.load_manifest('ros_p2p')
import rospy
from std_msgs.msg import String

import random, re, socket, Queue, time, select, getpass, sys, errno, fcntl, struct
from threading import Thread

import xmpp

import common
from stunclient import *
from parseconf import *


import sys

# global messages list
messages = []
sub_message = ''

def getIpAddress(ifname):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(fcntl.ioctl(
            s.fileno(),
            0x8915,  # SIOCGIFADDR
            struct.pack('256s', ifname[:15])
            )[20:24])

class ClientConf(ParseConf):
    '''client configuration'''
    #def getListenAddr(self):
    #    addr = self.getValue('listen')
    #    (h, _, p) = addr.partition(':')
    #    return (h, int(p))

    def getNetType(self):
        t = self.getValue('net_type')
        return int(t)
    
    def getSTUNServer(self):
        addr = self.getValue('stun_server')
        (h, _, p) = addr.partition(':')
        if p == '':
            return (h, common.STUN_DEF_PORT)
        return (h, int(p))
    
    def getGTalkServer(self):
        addr = self.getValue('gtalk_server')
        (h, _, p) = addr.partition(':')
        return (h, int(p))

    def getLoginInfo(self):
        u = self.getValue('i')
        p = getpass.getpass('Password for %s: ' % u)
        return (u, p)

    def getServerUser(self):
        return self.getValue('server_user') + '@gmail.com'

def xmppMessageCB(cnx, msg):
    u = msg.getFrom()
    m = msg.getBody()
    print u, m
    if u and m:
        messages.append((str(u).strip(), str(m).strip()))
        #messages.append((unicode(u), unicode(m)))

def xmppListen(gtalkServerAddr, user, passwd):
    cnx = xmpp.Client('gmail.com', debug=[])
    cnx.connect()
    cnx.auth(user, passwd)
    cnx.sendInitPresence()
    cnx.RegisterHandler('message', xmppMessageCB)
    return cnx

def gotReply(ms, user):
    while True:
        try:
            (u, c) = ms.pop(0)
        except IndexError:
            break
        # check client user
        if u.partition('/')[0] != user:
            continue
        return c
    return None

class ConnectError(Exception):
    def __init__(self, reason):
        self.reason = reason

    def __str__(self):
        return '<Connect Error: %s>' % self.reason

def sub_callback(data):
    sub_message = str(data)

def main(args):
    #listenAddr = None
    serverAddr = None
    fromAddr = None

    pub = rospy.Publisher('output', String)
    rospy.Subscriber('input', String, sub_callback)

    rospy.init_node('p2p_client')

    pre_sub_message = ''

    # open client configuration file
    ind = args[0].rfind('/')
    clientConf = ClientConf(args[0][0:ind]+'/../conf/client.conf')

    # get network type
    netType = clientConf.getNetType()
    if netType == NET_TYPE_UDP_BLOCKED:
        # blocked
        print 'UDP is blocked by the firewall, QUIT!'
        return
    
    # create listened socket 
    #listenSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
    #listenSock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    #listenAddr = clientConf.getListenAddr()
    #listenSock.bind(listenAddr)

    # create socket and get mapped address
    toSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
    toSock.settimeout(1)
    stunServerAddr = clientConf.getSTUNServer()
    sc = STUNClient()
    (mappedIP, mappedPort) = sc.getMappedAddr(toSock, stunServerAddr)

    # get gtalk server's addr
    gtalkServerAddr = clientConf.getGTalkServer()
    # get user info of xmpp(gtalk) 
    (user, passwd) = clientConf.getLoginInfo()
    serverUser = clientConf.getServerUser()

    # send client hello
    cnx = xmppListen(gtalkServerAddr, user, passwd)
    cnx.send(xmpp.Message(serverUser, 'Hello;%d;%s:%d;' % (netType, mappedIP, mappedPort)))
    # wait for reply
    ct = time.time()
    content = None
    while time.time() - ct < common.TIMEOUT:
        if not cnx.Process(1):
            raise ConnectError('XMPP lost connection')
        # process messages
        content = gotReply(messages, serverUser)

        if content:
            break
    if None == content:
        raise ConnectError('Timeout')

    # process reply
    print 'Input xmpp message:', content
    if re.match(r'^Cannot;[a-zA-Z0-9_\ \t]+;[a-z]{%d}$' % common.SESSION_ID_LENGTH, content):
        # Cannot
        raise ConnectError(content.split(';')[1])
    elif re.match(r'^Do;InL;\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5};[a-z]{%d}$' \
                  % common.SESSION_ID_LENGTH, content):
        #In same LAN
        # parse server reply
        ip = content.split(';')[2].split(':')[0]
        try:
            socket.inet_aton(ip)
        except socket.error:
            # invalid ip
            raise ConnectError('Invalid Server Reply')
        p = int(content.split(';')[2].split(':')[1])
        s = content.split(';')[3]
        #send xmpp message
        myip = getIpAddress('wlan0')
        cnx.send(xmpp.Message(serverUser, 'Ack;InL;%s:%d;%s' % (myip, common.DEF_INLAN_PORT,s)))
        # send client hi (udp)
        toSock.setblocking(True)
        print ip, ':',p
        toSock.sendto('Hi;%s' % s, (ip, p))
        # wait for server's 'Welcome' (udp)
        toSock.settimeout(1)
        ct = time.time()
        while time.time() - ct < common.TIMEOUT:
            try:
                (data, fro) = toSock.recvfrom(2048)
            except socket.timeout:
                continue
            # got some data
            if fro == (ip, p) and data == 'Welcome;%s' % s:
                # connection established
                serverAddr = fro
                break
        else:
            raise ConnectError('Timeout')

    elif re.match(r'^Do;IA;\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5};[a-z]{%d}$' \
                  % common.SESSION_ID_LENGTH, content):
        # IA, prepare to connect server
        # parse server reply
        ip = content.split(';')[2].split(':')[0]
        try:
            socket.inet_aton(ip)
        except socket.error:
            # invalid ip
            raise ConnectError('Invalid Server Reply')
        p = int(content.split(';')[2].split(':')[1])
        s = content.split(';')[3]
        # send client hi (udp)
        toSock.setblocking(True)
        toSock.sendto('Hi;%s' % s, (ip, p))
        # wait for server's 'Welcome' (udp)
        toSock.settimeout(1)
        ct = time.time()
        while time.time() - ct < common.TIMEOUT:
            try:
                (data, fro) = toSock.recvfrom(2048)
            except socket.timeout:
                continue
            # got some data
            if fro == (ip, p) and data == 'Welcome;%s' % s:
                # connection established
                serverAddr = fro
                break
        else:
            raise ConnectError('Timeout')
    elif re.match(r'^Do;IB;[a-z]{%d}$' % common.SESSION_ID_LENGTH, content):
        # IB, wait for server's request
        # parse server reply
        s = content.split(';')[2]
        # wait for server's 'Hi' (udp)
        toSock.settimeout(1)
        ct = time.time()
        while time.time() - ct < common.TIMEOUT:
            try:
                (data, fro) = toSock.recvfrom(2048)
            except socket.timeout:
                continue
            # got some data
            if data == 'Hi;%s' % s:
                # connection established
                serverAddr = fro
                # send client Welcome (udp)
                toSock.setblocking(True)
                toSock.sendto('Welcome;%s' % s, serverAddr)
                break
        else:
            raise ConnectError('Timeout')
    elif re.match(r'^Do;IIA;\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5};[a-z]{%d}$' \
                  % common.SESSION_ID_LENGTH, content):
        # IIA, prepare to connect server
        # parse server reply
        ip = content.split(';')[2].split(':')[0]
        try:
            socket.inet_aton(ip)
        except socket.error:
            # invalid ip
            raise ConnectError('Invalid Server Reply')
        p = int(content.split(';')[2].split(':')[1])
        s = content.split(';')[3]
        # send client hi (udp)
        toSock.setblocking(True)
        toSock.sendto('Hi;%s' % s, (ip, p))
        # wait for server's 'Welcome' (udp)
        toSock.settimeout(1)
        ct = time.time()
        while time.time() - ct < common.TIMEOUT:
            try:
                (data, fro) = toSock.recvfrom(2048)
            except socket.timeout:
                continue
            # got some data
            if fro == (ip, p) and data == 'Welcome;%s' % s:
                # connection established
                serverAddr = fro
                break
        else:
            raise ConnectError('Timeout')
    elif re.match(r'^Do;IIB;\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5};[a-z]{%d}$' \
                  % common.SESSION_ID_LENGTH, content):
        # IIB, punch and wait for server's request
        # parse server reply
        ip = content.split(';')[2].split(':')[0]
        try:
            socket.inet_aton(ip)
        except socket.error:
            # invalid ip
            raise ConnectError('Invalid Server Reply')
        p = int(content.split(';')[2].split(':')[1])
        s = content.split(';')[3]
        # punch
        toSock.setblocking(True)
        toSock.sendto('Punch', (ip, p))
        # send Ack (xmpp)
        cnx.send(xmpp.Message(serverUser, 'Ack;IIB;%s' % s))
        # wait for server's 'Hi' (udp)
        toSock.settimeout(1)
        ct = time.time()
        while time.time() - ct < common.TIMEOUT:
            try:
                (data, fro) = toSock.recvfrom(2048)
            except socket.timeout:
                continue
            # got some data
            if data == 'Hi;%s' % s:
                # connection established
                serverAddr = fro
                # send client Welcome (udp)
                toSock.setblocking(True)
                toSock.sendto('Welcome;%s' % s, serverAddr)
                break
        else:
            raise ConnectError('Timeout')
    elif re.match(r'^Do;III;\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5};[a-z]{%d}$' \
                  % common.SESSION_ID_LENGTH, content):
        # III, prepare to connect server
        # parse server reply
        ip = content.split(';')[2].split(':')[0]
        try:
            socket.inet_aton(ip)
        except socket.error:
            # invalid ip
            raise ConnectError('Invalid Server Reply')
        p = int(content.split(';')[2].split(':')[1])
        s = content.split(';')[3]
        # punch
        toSock.setblocking(True)
        toSock.sendto('Punch', (ip, p))
        # send Ack (xmpp)
        cnx.send(xmpp.Message(serverUser, 'Ack;III;%s' % s))
        # wait for server's 'Hi' (udp)
        toSock.settimeout(1)
        ct = time.time()
        while time.time() - ct < common.TIMEOUT:
            try:
                (data, fro) = toSock.recvfrom(2048)
            except socket.timeout:
                continue
            # got some data
            if fro == (ip, p) and data == 'Hi;%s' % s:
                # connection established
                serverAddr = fro
                # send client Welcome (udp)
                toSock.setblocking(True)
                toSock.sendto('Welcome;%s' % s, serverAddr)
                break
        else:
            raise ConnectError('Timeout')
    elif re.match(r'^Do;IVA;\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5};[a-z]{%d}$' \
                  % common.SESSION_ID_LENGTH, content):
        # IVA
        # parse server reply
        ip = content.split(';')[2].split(':')[0]
        try:
            socket.inet_aton(ip)
        except socket.error:
            # invalid ip
            raise ConnectError('Invalid Server Reply')
        p = int(content.split(';')[2].split(':')[1])
        s = content.split(';')[3]
        # new socket
        toSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        # punch
        toSock.setblocking(True)
        toSock.sendto('Punch', (ip, p))
        # get new socket's mapped addr
        toSock.settimeout(1)
        sc = STUNClient()
        (mappedIP, mappedPort) = sc.getMappedAddr(toSock, stunServerAddr)
        # tell server the new addr (xmpp)
        cnx.send(xmpp.Message(serverUser, 'Ack;IVA;%s:%d;%s' % (mappedIP, mappedPort, s)))
        # wait for server's 'Hi' (udp)
        toSock.settimeout(1)
        ct = time.time()
        while time.time() - ct < common.TIMEOUT:
            try:
                (data, fro) = toSock.recvfrom(2048)
            except socket.timeout:
                continue
            # got some data
            if fro == (ip, p) and data == 'Hi;%s' % s:
                # connection established
                serverAddr = fro
                # send client Welcome (udp)
                toSock.setblocking(True)
                toSock.sendto('Welcome;%s' % s, serverAddr)
                break
        else:
            raise ConnectError('Timeout')
    elif re.match(r'^Do;IVB;\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5};[a-z]{%d}$' \
                  % common.SESSION_ID_LENGTH, content):
        # IVB
        # parse server reply
        ip = content.split(';')[2].split(':')[0]
        try:
            socket.inet_aton(ip)
        except socket.error:
            # invalid ip
            raise ConnectError('Invalid Server Reply')
        port = int(content.split(';')[2].split(':')[1])
        s = content.split(';')[3]
        # send client hi (udp) to a port range
        bp = port - common.LOCAL_RANGE
        if bp < 1:
            bp = 1
        ep = port + common.LOCAL_RANGE
        if ep > 65536:
            ep = 65536
        toSock.setblocking(True)
        for p in range(bp, ep):
            toSock.sendto('Hi;%s' % s, (ip, p))
        # wait for server's 'Welcome' (udp)
        toSock.settimeout(1)
        ct = time.time()
        while time.time() - ct < common.TIMEOUT:
            try:
                (data, fro) = toSock.recvfrom(2048)
            except socket.timeout:
                continue
            # got some data
            if data == 'Welcome;%s' % s:
                # connection established
                serverAddr = fro
                break
        else:
            raise ConnectError('Timeout')
    elif re.match(r'^Do;VA;\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5};[a-z]{%d}$' \
                  % common.SESSION_ID_LENGTH, content):
        # VA
        # parse server reply
        ip = content.split(';')[2].split(':')[0]
        try:
            socket.inet_aton(ip)
        except socket.error:
            # invalid ip
            raise ConnectError('Invalid Server Reply')
        p = int(content.split(';')[2].split(':')[1])
        s = content.split(';')[3]
        # for all ports
        while True:
            # punch
            toSock.setblocking(True)
            toSock.sendto('Punch', (ip, p))
            # tell server we've punched
            cnx.send(xmpp.Message(serverUser, 'Ack;VA;%s' % s))
            # wait for DONE
            ct = time.time()
            while time.time() - ct < common.TIMEOUT:
                if not cnx.Process(1):
                    raise ConnectError('XMPP lost connection')
                # process messages
                content = gotReply(messages, serverUser)
                if content == 'Done;VASent;%s' % s:
                    break
            else:
                raise ConnectError('Timeout')
            # have we received server's hello?
            toSock.setblocking(False)
            established = False
            while True:
                try:
                    (data, fro) = toSock.recvfrom(2048)
                except socket.error, e:
                    if e[0] != errno.EAGAIN and e[0] != 10035:
                        raise e
                    # EAGAIN
                    break
                # got some data
                if data == 'Hi;%s' % s:
                    toSock.setblocking(True)
                    toSock.sendto('Welcome;%s' % s, fro)
                    serverAddr = fro
                    established = True
                    break
            # is it ok?
            if established:
                break
            print '.',
            sys.stdout.flush()
    elif re.match(r'^Do;VB;\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5};[a-z]{%d}$' \
                  % common.SESSION_ID_LENGTH, content):
        # VB
        # parse server reply
        ip = content.split(';')[2].split(':')[0]
        try:
            socket.inet_aton(ip)
        except socket.error:
            # invalid ip
            raise ConnectError('Invalid Server Reply')
        srcPort = int(content.split(';')[2].split(':')[1])
        s = content.split(';')[3]
        # scan all ports of the server
        portBegin = 1
        while portBegin < 65536:
            # try to connect server's port range
            toSock.setblocking(True)
            for p in range(portBegin, portBegin + common.SYM_SCAN_RANGE):
                if p < 65536:
                    # send client hi (udp)
                    port = (p + srcPort - common.SYM_SCAN_PRE_OFFSET) % 65536
                    toSock.sendto('Hi;%s' % s, (ip, port))
            portBegin = p + 1
            # tell server we've sent Hi
            cnx.send(xmpp.Message(serverUser, 'Ack;VB;%s' % s))
            #print 'Ack Sent, end port = %d.' % port
            #cnx.sendPresence()
            # wait for any message, both udp and xmpp.
            toSock.setblocking(False)
            established = False
            ct = time.time()
            while time.time() - ct < common.TIMEOUT:
                if not cnx.Process(1):
                    raise ConnectError('XMPP lost connection')
                # did we receive server's 'Welcome'(udp)?
                try:
                    (data, fro) = toSock.recvfrom(2048)
                    # got some data
                    if data == 'Welcome;%s' % s:
                        # connection established
                        serverAddr = fro 
                        established = True
                        break
                except socket.error, e:
                    if e[0] != errno.EAGAIN and e[0] != 10035:
                        raise e
                    # EAGAIN, ignore
                # process messages
                content = gotReply(messages, serverUser)
                if content:
                    break
            else:
                raise ConnectError('Timeout')
            # is it ok?
            if established:
                break
            print '.',
            sys.stdout.flush()
        else:
            raise ConnectError('Failed to try')
    else:
        # wrong reply
        raise ConnectError('Invalid Server Reply')

    print 'Connection established.'
    # non-blocking IO
    #listenSock.setblocking(False)
    toSock.setblocking(False)
    lastCheck = time.time()
    # transfer
    while True:
        # check listenSock/toSock
        #(rs, _, es) = select.select([listenSock, toSock], [], [], 1)
        (rs, _, es) = select.select([toSock], [], [], 1)
        if len(es) != 0:
            # error
            print 'Transfer error.'
            return
        #if listenSock in rs:
        #    #print 'listenSock has got some data:', 
        #    # listenSock is ready for read
        #    while True:
        #        try:
        #            (d, fromAddr) = listenSock.recvfrom(2048)
        #            #print d
        #        except socket.error, e:
        #            if e[0] != errno.EAGAIN and e[0] != 10035:
        #                raise e
        #            # EAGAIN
        #            break
        if pre_sub_message != sub_message:
            toSock.sendto(sub_message, serverAddr)
        if toSock in rs:
            #print 'toSock has got some data:', 
            # toSock is ready for read
            while True:
                try:
                    (d, a) = toSock.recvfrom(2048)
                    if d == '':
                        # preserve connection
                        continue
                    #print d
                except socket.error, e:
                    if e[0] != errno.EAGAIN and e[0] != 10035:
                        raise e
                    # EAGAIN
                    break
                if a == serverAddr:
                    pub.publish(String(d))
        # preserve connection
        t = time.time()
        if t - lastCheck >= 1:
            lastCheck = t
            toSock.sendto('', serverAddr)

if __name__ == '__main__':
    main(sys.argv)
