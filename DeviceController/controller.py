#!/usr/bin/env python2
################################################################################
# @file           controller.py
#
# @author         Michael Catanzaro <michael.catanzaro@mst.edu>
#
# @project        FREEDM Fake Device Controller
#
# @description    A fake ARM controller for FREEDM devices.
#
# These source code files were created at Missouri University of Science and
# Technology, and are intended for use in teaching or research. They may be
# freely copied, modified, and redistributed as long as modified versions are
# clearly marked as such and this notice is not removed. Neither the authors
# nor Missouri S&T make any warranty, express or implied, nor assume any legal
# responsibility for the accuracy, completeness, or usefulness of these files
# or any information distributed with these files.
#
# Suggested modifications or questions about these files can be directed to
# Dr. Bruce McMillin, Department of Computer Science, Missouri University of
# Science and Technology, Rolla, MO 65409 <ff@mst.edu>.
################################################################################

import ConfigParser
import argparse
import datetime
import math
import os
import socket
import string
import sys
import time

config = {}

COPYRIGHT_YEAR = '2013'
ERROR_FILE = 'ERRORS'
VERSION_FILE = '../Broker/src/version.h'


def initializeConfig():
    """
    Called at the start of main to initialize the global configuration using
    ConfigParser and argparse.
    """
    global config

    desc = \
'''A fake FREEDM device controller that interfaces with the DGI via the FREEDM
plug and play protocol and pretends to immediately implement DGI commands.'''

    epi = 'Batteries not included.'

    parser = argparse.ArgumentParser(description=desc, epilog=epi)
    parser.add_argument('-c', '--config', default='controller.cfg',
                        help='file to use for additional configuration')
    parser.add_argument('-g', '--host',
                        help='hostname of the DGI to connect to')
    parser.add_argument('-n', '--name',
                        help='unique identifier of this controller')
    parser.add_argument('-p', '--port', type=int,
                        help="the DGI AdapterFactory's listening port")
    parser.add_argument('-V', '--version', action='store_true',
                        help='print version info and exit')
    parser.add_argument('-s', '--script', help='the DSP script file to run')
    args = parser.parse_args()

    if args.version:
        version = '(Unknown Version)'
        try:
            with open(VERSION_FILE, 'r') as versionFile:
                versionLine = versionFile.readlines()[2]
                versionStart = versionLine.index('"')
                versionEnd = versionLine.rindex('"')
                version = versionLine[versionStart+1:versionEnd]
        except:
            pass
        print 'FREEDM Fake Device Controller Revision', version
        print 'Copyright (C)', COPYRIGHT_YEAR, 'NSF FREEDM Systems Center'
        sys.exit(0)

    if args.name:
        config['name'] = args.name
    if args.script:
        config['script'] = args.script
    if args.host:
        config['host'] = args.host
    if args.port:
        config['port'] = args.port

    configFileName = 'controller.cfg'
    if args.config:
        configFileName = args.config

    # read connection settings from the config file
    with open(configFileName, 'r') as configFile:
        cp = ConfigParser.SafeConfigParser()
        cp.readfp(configFile)

    if args.name == None:
        config['name'] = cp.get('controller', 'name')
    if args.script == None:
        config['script'] = cp.get('controller', 'script')
    if args.host == None:
        config['host'] = cp.get('connection', 'host')
    if args.port == None:
        config['port'] = int(cp.get('connection', 'port'))
    config['state-timeout'] = float(cp.get('timings', 'state-timeout'))/1000.0
    config['hello-timeout'] = float(cp.get('timings', 'hello-timeout'))/1000.0
    config['dgi-timeout'] = float(cp.get('timings', 'dgi-timeout'))/1000.0
    config['adapter-connection-retries'] = \
            int(cp.get('misc', 'adapter-connection-retries'))


def sendAll(socket, msg):
    """
    Ensures the entirety of msg is sent over the socket.

    @param socket the connected stream socket to send to
    @param msg the message to be sent

    @ErrorHandling Could raise socket.error or socket.timeout if the data
                   cannot be sent in time.
    """
    assert len(msg) != 0
    sentBytes = socket.send(msg)
    while (sentBytes != len(msg)):
        assert sentBytes < len(msg)
        msg = msg[sentBytes:]
        assert len(msg) != 0
        sentBytes = socket.send(msg)
        if sentBytes == 0:
            raise socket.error('Connection to DGI unexpectedly closed')


def recvAll(socket):
    """
    Receives all the data that has been sent from DGI until \r\n\r\n is reached.
    
    @param socket the connected stream socket to receive from

    @ErrorHandling Will raise a RuntimeError if there is data after \r\n\r\n,
                   since that delimits the end of a message and the DGI is not
                   allowed to send multiple messages in a row. Also raises
                   runtime errors if there is no \r\n\r\n at all in the packet,
                   or if it doesn't occur at the very end of the packet. Will
                   raise socket.timeout if the DGI times out, or socket.error if
                   the connection is closed.

    @return the data that has been read
    """
    msg = socket.recv(1024)
    while len(msg)%1024 == 0:
        msg += socket.recv(1024)
    if len(msg) == 0:
        raise socket.error('Connection to DGI unexpectedly closed')
    if msg.find('\r\n\r\n') != len(msg)-4:
        raise RuntimeError('Malformed message from DGI:\n' + msg)
    return msg


def handleBadRequest(msg):
    """
    If the DGI says we sent a bad request, log the error and try to reconnect.
    You probably need to return immediately after calling this.

    @param msg string the message sent by DGI
    """
    msg = msg.replace('BadRequest', '', 1)
    errormsg = 'Sent bad request to DGI: ' + msg
    print >> sys.stderr, errormsg
    with open(ERROR_FILE, 'a') as errorfile:
        errorfile.write(str(datetime.datetime.now()) + '\n')
        errorfile.write(errormsg)


def enableDevice(deviceTypes, deviceSignals, command):
    """
    Add a new device to the list of devices. Takes a map that represents the
    previous devices in the system and a command from the DSP script, and adds
    the new device and its signals to the map.

    @param deviceTypes dict of names -> types
    @param deviceSignals dict of device (name, signal) -> float
    @param command string from the dsp-script
    """
    command = command.split()
    assert command[0] == 'enable'
    assert (len(command)-3)%2 == 0 and len(command) >= 5
    name = command[2]
    if deviceTypes.has_key(name):
        raise AssertionError('Device ' + name + ' is already enabled!')
    deviceTypes[name] = command[1]
    for i in range (3, len(command), 2):
        deviceSignals[(name, command[i])] = command[i+1]


def disableDevice(deviceTypes, deviceSignals, command):
    """
    Remove a device from the list of devices. Operates just like enableDevice
    (see above), except in reverse.

    @param deviceTypes dict of names -> types
    @param deviceSignals dict of device (name, signal) -> float
    @param command string from the dsp-script
    """
    command = command.split()
    assert command[0] == 'disable'
    assert len(command) == 2

    assert deviceTypes.has_key(command[1])
    del deviceTypes[command[1]]

    removedSomething = False
    for name, signal in deviceSignals.keys():
        if name == command[1]:
            del deviceSignals[(name, signal)]
            removedSomething = True
    assert removedSomething


def sendStates(adapterSock, deviceSignals):
    """
    Sends updated device states to the DGI.

    @param adapterSock the connected stream socket to send the states on
    @param deviceSignals dict of (name, signal) pairs to floats

    @ErrorHandling caller must check for socket.timeout
    """
    msg = 'DeviceStates\r\n'
    for (name, signal) in deviceSignals.keys():
        msg += name + ' ' + signal + ' ' + str(deviceSignals[(name, signal)])
        msg += '\r\n'
    msg += '\r\n'
    print 'Sending states to DGI:\n' + msg.strip()
    sendAll(adapterSock, msg)
    print 'Sent states to DGI\n'


def receiveCommands(adapterSock, deviceSignals):
    """
    Receives new commands from the DGI and then implements them by modifying the
    devices map. We're basically the best controller ever since we satify the
    DGI instantanously.

    @param adapterSock the connected stream socket to receive commands from
    @param deviceSignals dict of (name, signal) pairs to floats

    @ErrorHandling caller must check for socket.timeout
    """
    print 'Awaiting commands from DGI...'
    msg = recvAll(adapterSock)
    print 'Received commands from DGI:\n' + msg.strip()
    if msg.find('DeviceCommands\r\n') != 0:
        raise RuntimeError('Malformed command packet:\n' + msg)
    for line in msg.split('\r\n'):
        if line.find('DeviceCommands') == 0:
            continue
        command = line.split()
        if len(command) == 0:
            continue
        if len(command) != 3:
            raise RuntimeError('Malformed command in packet:\n' + line)
        if not deviceSignals.has_key((command[0], command[1])):
            raise RuntimeError('Unrecognized command in packet:\n' + line)
        # Implement the command *unless* DGI doesn't really know our states.
        if not math.isnan(float(command[2])):
            deviceSignals[(command[0], command[1])] = command[2]
    print 'Device states have been updated\n'


def work(adapterSock, deviceSignals):
    """
    Send states, receive commands, then sleep for a bit.
    
    @param adapterSock the connected stream socket to use
    @param deviceSignals dict of (device, name) pairs to floats

    @ErrorHandling caller must check for socket.timeout
    """
    global config
    sendStates(adapterSock, deviceSignals)
    receiveCommands(adapterSock, deviceSignals)
    time.sleep(config['state-timeout'])


def reconnect(deviceTypes):
    """
    Sends a Hello message to DGI, and receives its Start message in response.
    If there is a failure, tries again until it works.

    @param deviceTypes dict of device names -> types
    
    @return a stream socket connected to a DGI adapter
    """
    global config
    
    devicePacket = 'Hello\r\n'
    devicePacket += config['name'] + '\r\n'
    for deviceName, deviceType in deviceTypes.items():
        devicePacket += deviceType + ' ' + deviceName + '\r\n'
    devicePacket += '\r\n'
    
    msg = ''
    while True:
        adapterFactorySock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        adapterFactorySock.settimeout(config['dgi-timeout'])
        try:
            print 'Attempting to connect to AdapterFactory...'
            adapterFactorySock.connect((config['host'], config['port']))
        except (socket.error, socket.herror, socket.gaierror, socket.timeout) \
                as e:
            print >> sys.stderr, \
                'Fail connecting to AdapterFactory: {0}'.format(e.strerror)
            time.sleep(config['hello-timeout'])
            print >> sys.stderr, 'Attempting to reconnect...'
            continue
        else:
            print 'Connected to AdapterFactory'

        try:
            sendAll(adapterFactorySock, devicePacket)
            print '\nSent Hello message:\n' + devicePacket.strip()
            msg = recvAll(adapterFactorySock)
            print '\nReceived Start message:\n' + msg.strip()
        except (socket.error, socket.timeout) as e:
            print >> sys.stderr, \
                'AdapterFactory communication failure: {0}'.format(e.strerror)
            adapterFactorySock.close()
            time.sleep(config['hello-timeout'])
            print >> sys.stderr, 'Attempting to reconnect...'
        else:
            break

    if msg.find('BadRequest') == 0:
        handleBadRequest(msg)
        time.sleep(config['hello-timeout'])
        return reconnect(deviceTypes)
    else:
        msg = msg.split()
        if len(msg) != 3 or msg[0] != 'Start' or msg[1] != 'StatePort:':
            raise RuntimeError('DGI sent malformed Start:\n' + ' '.join(msg))

    adapterPort = int(msg[2])
    if 0 < adapterPort < 1024:
        raise ValueError('DGI wants to use DCCP well known port ' + adapterPort)
    elif adapterPort < 0 or adapterPort > 65535:
        raise ValueError('DGI sent a nonsense statePort ' + config['port'])

    for i in range(0, config['adapter-connection-retries']):
        try:
            adapterSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            adapterSocket.connect((config['host'], adapterPort))
        except (socket.error, socket.herror, socket.gaierror, socket.timeout) \
                as e:
            print >> sys.stderr, \
                'Error connecting to DGI adapter: {0}'.format(e.strerror)
            time.sleep(config['state-timeout'])
            if i == 9:
                print >> sys.stderr, \
                    'Giving up on the adapter, sending a new Hello'
                return reconnect(deviceTypes)
            else:
                print >> sys.stderr, 'Trying again...'
        else:
            # FIXME for some reason this doesn't prevent hangs when dgi dies
            # Figuring out why would be good. But none of the other code ever
            # touches anything besides this socket. =/
            adapterSocket.settimeout(config['dgi-timeout'])
            return adapterSocket

def politeQuit(adapterSock, deviceSignals):
    """
    Sends a quit request to DGI and returns once the request has been approved.
    If the request is not initially approved, continues to send device states
    and receive device commands until the request is approved or the DGI times
    out, at which point control returns to the script.

    @param adapterSock the connected stream socket which wants to quit
                       THIS SOCKET WILL BE CLOSED!!
    @param deviceSignals dict of device (name, signal) pairs -> floats
    """
    global config

    while True:
        try:
            print 'Sending PoliteDisconnect request to DGI'
            sendAll(adapterSock, 'PoliteDisconnect\r\n\r\n')
            msg = recvAll(adapterSock)
        except (socket.error, socket.timeout) as e:
            print >> sys.stderr, \
                'DGI communication error: {0}'.format(e.strerror)
            print >> sys.stderr, 'Closing connection impolitely'
            adapterSock.close()
            return

        if msg.find('BadRequest') == 0:
            handleBadRequest(msg)
            return

        msg = msg.split()
        if len(msg) != 2 or msg[0] != 'PoliteDisconnect' or \
                (msg[1] != 'Accepted' and msg[1] != 'Rejected'):
            raise RuntimeError('Got bad disconnect response:\n' + ' '.join(msg))

        if msg[1] == 'Accepted':
            print 'PoliteDisconnect accepted by DGI'
            adapterSock.close()
            return
        else:
            assert msg[1] == 'Rejected'
            print 'PoliteDisconnect rejected, performing another round of work'
            try:
                work(adapterSock, deviceSignals, config['state-timeout'])
                # Loop again
            except (socket.error, socket.timeout) as e:
                print >> sys.stderr, \
                    'DGI communication error: {0}'.format(e.strerror)
                print >> sys.stderr, 'Closing connection impolitely'
                adapterSock.close()
                return


if __name__ == '__main__': 
    # dict of names -> types
    deviceTypes = {}
    # dict of (name, signal) pairs -> strings (representing floats :S)
    deviceSignals = {}
    # are we processing the very first command in the script?
    firstHello = True
    # socket connected
    adapterSock = -1

    # we don't want to see errors from previous runs
    try:
        os.remove(ERROR_FILE)
    except OSError:
        # no errors, yay
        pass
    
    initializeConfig() 
    script = open(config['script'], 'r')
    for command in script:
        # These dictionaries must be kept in parallel
        assert len(deviceTypes) == len(deviceSignals)

        # If we ever need to break connection, we will reconnect immediately.
        # So after the first Hello, adapterSock will ALWAYS be valid.
        if not firstHello:
            assert adapterSock.fileno() > 1

        if command[0] == '#' or command.isspace(): 
            continue

        print '\nProcessing command: ' + command.strip()

        if command.find('enable') == 0 and (len(command.split())-3)%2 == 0:
            enableDevice(deviceTypes, deviceSignals, command)
            if not firstHello:
                politeQuit(adapterSock, deviceSignals)
            adapterSock = reconnect(deviceTypes)
            if firstHello:
                firstHello = False

        elif command.find('disable') == 0 and len(command.split()) == 2:
            if firstHello:
                raise RuntimeError("Can't disable devices before first Hello")
            disableDevice(deviceTypes, deviceSignals, command)
            politeQuit(adapterSock, deviceSignals)
            adapterSock = reconnect(deviceTypes)
            
        elif command.find('change') == 0 and len(command.split()) == 4:
            if firstHello:
                raise RuntimeError("Can't change a signal before first Hello")
            command = command.split()
            deviceSignals[(command[1], command[2])] = command[3]
            print 'Changed', command[1], command[2], 'to', command[3]

        elif command.find('dieHorribly') == 0 and len(command.split()) == 2:
            if firstHello:
                raise RuntimeError("Can't die before first Hello")
            duration = command.split()[1]
            if duration == 'forever':
                print 'I have died horribly forever, goodbye'
                script.close()
                sys.exit(0)
            duration = int(duration)
            if duration < 0:
                raise ValueError("It's nonsense to die for " + duration + "s")
            time.sleep(duration)
            print 'Back to life!'
            adapterSock.close()
            adapterSock = reconnect(deviceTypes)

        elif command.find('work') == 0 and len(command.split()) == 2:
            if firstHello:
                raise RuntimeError("Can't work before the first Hello")
            duration = command.split()[1]
            if duration == 'forever':
                while True:
                    try:
                        work(adapterSock, deviceSignals)
                    except (socket.error, socket.timeout) as e:
                        print >> sys.stderr, \
                            'DGI communication error: {0}'.format(e.strerror)
                        print >> sys.stderr, 'Performing impolite reconnect'
                        adapterSock.close()
                        adapterSock = reconnect(deviceTypes)
            elif int(duration) <= 0:
                raise ValueError('Nonsense to work for ' + duration + 's')
            else:
                for i in range(int(duration)):
                    try:
                        print 'Performing work {0} of {1}\n'.format(i, duration)
                        work(adapterSock, deviceSignals)
                    except (socket.error, socket.timeout) as e:
                        print >> sys.stderr, \
                            'DGI communication error: {0}'.format(e.strerror)
                        print >> sys.stderr, 'Performing impolite reconnect'
                        adapterSock.close()
                        adapterSock = reconnect(deviceTypes)

        else:
            raise RuntimeError('Read invalid script command:\n' + command)

    print 'That seems to be the end of my script, disconnecting now...'
    politeQuit(adapterSock, deviceSignals)
    script.close()
