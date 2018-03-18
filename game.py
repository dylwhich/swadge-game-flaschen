#!/usr/bin/env python3
"""
A simple demonstration game for the MAGLabs 2017 Swadge.
"""

from autobahn.asyncio.wamp import ApplicationSession, ApplicationRunner
from autobahn.wamp import auth
from collections import deque
import itertools
import flaschen
import asyncio
import random
import time


class Button:
    """ Button name constants"""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    SELECT = "select"
    START = "start"
    A = "a"
    B = "b"


class Color:
    """Some common colors"""
    RED = 0xff0000
    ORANGE = 0xff7f00
    YELLOW = 0xffff00
    GREEN = 0x00ff00
    CYAN = 0x00ffff
    BLUE = 0x0000ff
    PURPLE = 0x7f00ff
    PINK = 0xff00ff

    WHITE = 0xffffff
    BLACK = 0x000000
    OFF = 0x000000

    RAINBOW = [RED, ORANGE, YELLOW, GREEN, CYAN, BLUE, PURPLE]

def hex_to_rgb(color):
    return ((color & 0xff0000) >> 16, (color & 0x00ff00) >> 8, color & 0x0000ff)


def lighten(amt, color):
    """
    Lighten a color by a percent --
    :param amt:
    :param color:
    :return:
    """
    return int(amt * ((color >> 16) & 0xff)) << 16 \
           | int(amt * ((color >> 8) & 0xff)) << 8 \
           | int(amt * (color & 0xff)) & 0xff


# WAMP Realm; doesn't change
WAMP_REALM = "swadges"
WAMP_URL = "ws://api.swadge.com:1337/ws"

# WAMP Credentials; you will get your own later
WAMP_USER = "demo"
WAMP_PASSWORD = "hunter2"

# This is a unique name for this game
# Change this before you run it, otherwise it will conflict!
GAME_ID = "sign-game"

# This is a unique button sequence a swadge can enter to join this game.
# This can be changed at any time, as long as the new value is unique.
# Setting this to the empty string will disable joining by sequence.
# Maximum length is 12; 6 is recommended.
# Buttons are [u]p, [l]eft, [d]own, [r]ight, s[e]lect, [s]tart, [a], [b]
GAME_JOIN_SEQUENCE = "uuuddba"

# This is the name of a location that will cause a swadge to automatically
# join the game without needing to press any buttons. They will also leave
# the game when they are no longer in the location. Setting this to the
# empty string will disable joining by location. If this is set along with
# join by sequence, either of them being triggered will result in a join.
# Note that only one game can "claim" a location at once, so unless you need
# exclusive control over the location, you should just subscribe to that
# location.
#
# Current tracked locations (more may be added; if you'd like to make sure
# a location will be tracked, ask in #circuitboards):
# - panels1
# - gameroom
# - concerts
GAME_JOIN_LOCATION = None

WIDTH = 512
HEIGHT = 32

class PlayerInfo:
    COLOR_WHEEL = itertools.cycle(Color.RAINBOW)

    def __init__(self, badge_id, subscriptions=None):
        self.badge_id = badge_id

        self.color = next(PlayerInfo.COLOR_WHEEL)

        # Keep track of what the lights are set to
        self.light_settings = [self.color] * 4

        self.reset()

        # Subscriptions that have been made for the player
        # Needed so we can unsubscribe later
        self.subscriptions = subscriptions or []

    @property
    def position(self):
        return self.trail[-1]

    def reset(self):
        x, _ = initial_pos = (random.randrange(WIDTH), random.randrange(HEIGHT))
        if x > WIDTH//2:
            self.direction = 'l'
        else:
            self.direction = 'r'

        self.trail = deque()
        self.trail.append(initial_pos)

        self.dead = False

        self.brightness = 1

        self.moved = False

    def up(self):
        if self.direction == 'd': return
        if self.moved: return
        self.direction = 'u'
        self.moved = True

    def down(self):
        if self.direction == 'u': return
        if self.moved: return
        self.direction = 'd'
        self.moved = True

    def left(self):
        if self.direction == 'r': return
        if self.moved: return
        self.direction = 'l'
        self.moved = True

    def right(self):
        if self.direction == 'l': return
        if self.moved: return
        self.direction = 'r'
        self.moved = True

    async def move(self, players):
        if self.dead:
            return

        dx, dy = 0, 0
        if self.direction == 'u':
            dy = -1
        elif self.direction == 'd':
            dy = 1
        elif self.direction == 'l':
            dx = -1
        elif self.direction == 'r':
            dx = 1

        x, y = self.position
        npos = (x+dx, y+dy)
        nx, ny = npos

        if nx < 0 or nx >= WIDTH or ny < 0 or ny >= HEIGHT or any((npos in p.trail for p in players)):
            self.dead = True
            self.brightness = 0
            return

        self.trail.append(npos)
        self.moved = False

    def draw(self, fb):
        for x, y in self.trail:
            color = hex_to_rgb(self.color)
            fb.set(x, y, color)

class GameComponent(ApplicationSession):
    players = {}

    def onConnect(self):
        """
        Called by WAMP upon successfully connecting to the crossbar server
        :return: None
        """
        self.join(WAMP_REALM, ["wampcra"], WAMP_USER)

    def onChallenge(self, challenge):
        """
        Called by WAMP for authentication.
        :param challenge: The server's authentication challenge
        :return:          The client's authentication response
        """
        if challenge.method == "wampcra":
            signature = auth.compute_wcs(WAMP_PASSWORD.encode('utf8'),
                                         challenge.extra['challenge'].encode('utf8'))
            return signature.decode('ascii')
        else:
            raise Exception("don't know how to handle authmethod {}".format(challenge.method))

    async def game_register(self):
        """
        Register the game with the server. Should be called after initial connection and any time
        the server requests it.
        :return: None
        """

        res = await self.call('game.register',
                              GAME_ID,
                              sequence=GAME_JOIN_SEQUENCE,
                              location=GAME_JOIN_LOCATION)

        err = res.kwresults.get("error", None)
        if err:
            print("Could not register:", err)
        else:
            # This call returns any players that may have already joined the game to ease restarts
            players = res.kwresults.get("players", [])
            await asyncio.gather(*(self.on_player_join(player) for player in players))

    async def on_button_release(self, button, timestamp=0, badge_id=None):
        """
        Called when a button is released.
        :param button:   The name of the button that was released
        :param badge_id: The ID of the badge that released the button
        :return: None
        """

        player = self.players.get(badge_id, None)

        if not player:
            print("Unknown player:", badge_id)
            return

        # Do something with button released here

    async def set_lights(self, player):
        # Set the lights for the badge to simple colors
        # Note that the order of the lights will be [BOTTOM_LEFT, BOTTOM_RIGHT, TOP_RIGHT, TOP_LEFT]
        self.publish('badge.' + str(player.badge_id) + '.lights_static',
                     *(lighten(player.brightness, c) for c in player.light_settings))

                
    async def on_button_press(self, button, timestamp=0, badge_id=None):
        """
        Called when a button is pressed.
        :param button:   The name of the button that was pressed
        :param badge_id: The ID of the badge that pressed the button
        :return: None
        """

        player = self.players.get(badge_id, None)

        if not player:
            print("Unknown player:", badge_id)
            return

        if button == Button.UP:
            player.up()
        elif button == Button.DOWN:
            player.down()
        elif button == Button.LEFT:
            player.left()
        elif button == Button.RIGHT:
            player.right()
        elif button == Button.A:
            pass
        elif button == Button.B:
            pass
        elif button == Button.SELECT:
            self.publish('badge.' + str(badge_id) + '.text', 0, 0, 'Hi!', style=1)

    async def on_player_join(self, badge_id):
        """
        Called when a player joins the game, such as by entering a join sequence or entering a
        designated location.
        :param badge_id: The badge ID of the player who left
        :return: None
        """

        print("Badge #{} joined".format(badge_id))

        # Listen for button presses and releases
        press_sub = await self.subscribe(self.on_button_press, 'badge.' + str(badge_id) + '.button.press')

        # If you want to listen for button releases too, un-comment this and add release_sub to
        # the list of subscriptions below
        #release_sub = await self.subscribe(self.on_button_release, 'badge.' + str(badge_id) + '.button.release')

        # Add an entry to keep track of the player's game-state
        self.players[badge_id] = PlayerInfo(badge_id, subscriptions=[press_sub])

        await self.set_lights(self.players[badge_id])

        # Give the supporters a nice message on their screen
        self.publish('badge.' + str(badge_id) + '.text', 0, 24, "THANK YOU for supporting us!", style=1)

    async def on_player_leave(self, badge_id):
        """
        Called when a player leaves the game, such as by leaving a designated location.
        :param badge_id: The badge ID of the player who left
        :return: None
        """

        # Make sure we unsubscribe from all this badge's topics
        print("Badge #{} left".format(badge_id))
        await asyncio.gather(*(s.unsubscribe() for s in self.players[badge_id].subscriptions))
        del self.players[badge_id]

    async def onJoin(self, details):
        """
        WAMP calls this after successfully joining the realm.
        :param details: Provides information about
        :return: None
        """

        self.screen = flaschen.Flaschen('scootaloo.hackafe.net', 1337, 512, 32, 16, True)

        # Subscribe to all necessary things
        await self.subscribe(self.on_player_join, 'game.' + GAME_ID + '.player.join')
        await self.subscribe(self.on_player_leave, 'game.' + GAME_ID + '.player.leave')
        await self.subscribe(self.game_register, 'game.request_register')
        await self.game_register()

        while True:
            while len(self.players) < 2:
                for player in self.players.values():
                    player.draw(self.screen)
                    self.screen.send()
                await asyncio.sleep(.5)

            await asyncio.sleep(2)
            self.screen.clear()

            while sum((not p.dead for p in self.players.values())) > 1:
                for player in self.players.values():
                    player.draw(self.screen)
                    await player.move(self.players.values())
                    await self.set_lights(player)
                self.screen.send()

                await asyncio.sleep(.05)

            for _ in range(10):
                for on in (True, False):
                    self.screen.clear()
                    for player in self.players.values():
                        if not player.dead:
                            player.brightness = 1 if on else 0

                            if on:
                                player.draw(self.screen)
                        else:
                            player.draw(self.screen)

                    self.screen.send()
                    await asyncio.sleep(.1)

            self.screen.clear()
            self.screen.send()

            for player in self.players.values():
                player.reset()
            

    def onDisconnect(self):
        """
        Called when the WAMP connection is disconnected
        :return: None
        """
        asyncio.get_event_loop().stop()


if __name__ == '__main__':
    if GAME_ID == 'demo_game':
        print("Please change GAME_ID to something else!")
        exit(1)

    runner = ApplicationRunner(
        WAMP_URL,
        WAMP_REALM,
    )
    runner.run(GameComponent, log_level='info')
