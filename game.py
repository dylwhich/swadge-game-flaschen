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

TORUS_H = False
TORUS_V = False

DOT_COLORS = [Color.BLUE, Color.RED, Color.GREEN, Color.PURPLE, Color.CYAN, Color.ORANGE, Color.YELLOW, Color.PINK, Color.WHITE]

def dxdy_to_dir(dx, dy):
    if dx > 0:
        return 'r'
    elif dx < 0:
        return 'l'
    elif dy > 0:
        return 'd'
    elif dy < 0:
        return 'u'

def dir_to_dxdy(direction):
    if direction == 'r':
        return (1, 0)
    elif direction == 'l':
        return (-1, 0)
    elif direction == 'd':
        return (0, 1)
    elif direction == 'u':
        return (0, -1)

def dir_to_num(direction):
    return {
        'u': 0,
        'r': 1,
        'd': 2,
        'l': 3,
    }[direction.lower()]

def num_to_dir(direction):
    return ['u', 'r', 'd', 'l'][direction % 4]

class Powerup:
    def __init__(self, x, y, kind):
        self.kind = kind
        self.x = x
        self.y = y
        self.position = (x, y)
        self.consumed = False
        self.activated = False
        self.ticks = 1
        self.color = Color.WHITE

    def activate(self, player):
        self.activated = True

    def activate_secondary(self, player):
        pass

    def consume(self):
        self.consumed = True

    @property
    def exhausted(self):
        return not bool(self.ticks)

    def tick(self):
        if self.ticks:
            self.ticks -= 1

    def draw(self, fb):
        if not self.consumed:
            fb.set(self.x, self.y, hex_to_rgb(self.color))

    def done(self, player):
        pass

    def __str__(self):
        return self.kind

class JumpPowerup(Powerup):
    def __init__(self, x, y):
        super().__init__(x, y, 'Jump')
        self.color = Color.WHITE

class SpeedPowerup(Powerup):
    def __init__(self, x, y):
        super().__init__(x, y, 'Speed')
        self.ticks = 30
        self.color = Color.GREEN

    def activate(self, player):
        super().activate(player)
        player.moves = 2

    def done(self, player):
        player.moves = 1

class PortalPowerup(Powerup):
    def __init__(self, x, y):
        super().__init__(x, y, 'Portal')
        self.color = Color.ORANGE
        self._activated = (False, False)
        self.orange_deployed = False
        self.blue_deployed = False
        self.orange_activated = False
        self.blue_activated = False
        self.orange_portal = None
        self.blue_portal = None

    @property
    def exhausted(self):
        return self.orange_deployed and self.blue_deployed

    @property
    def activated(self):
        return self.orange_activated or self.blue_activated

    @activated.setter
    def activated(self, val):
        pass

    def activate(self, player):
        self.orange_activated = True

    def activate_secondary(self, player):
        self.blue_activated = True

    def set_orange(self, portal):
        self.orange_deployed = True
        self.orange_portal = portal

        if self.blue_portal:
            self.blue_portal.link(self.orange_portal)
            self.orange_portal.link(self.blue_portal)

    def set_blue(self, portal):
        self.blue_deployed = True
        self.blue_portal = portal

        if self.orange_portal:
            self.orange_portal.link(self.blue_portal)
            self.blue_portal.link(self.orange_portal)

class Entity:
    def __init__(self, x, y, kind):
        self.x = x
        self.y = y
        self.position = (x, y)
        self.kind = kind

    def draw(self, fb):
        pass

    def collide(self, x, y):
        return False

class Portal(Entity):
    def __init__(self, x, y, direction, color):
        super().__init__(x, y, 'Portal')

        self.direction = direction
        self.other = None
        self.color = color

    def points(self):
        horiz = [(self.x + n, self.y) for n in range(-2, 3)]
        vert = [(self.x, self.y + n) for n in range(-2, 3)]

        if self.direction == 'r':
            return vert
        elif self.direction == 'l':
            return list(reversed(vert))
        elif self.direction == 'u':
            return horiz
        elif self.direction == 'd':
            return list(reversed(horiz))

    def collide(self, x, y):
        return (x, y) in self.points()

    def calculate_path(self, x, y, dx, dy):
        if self.linked:
            if self.collide(x, y):
                dirnum = dir_to_num(dxdy_to_dir(dx, dy))
                diff = dirnum - dir_to_num(self.direction)
                new_dir = num_to_dir(dir_to_num(self.other.direction) + diff + 2)
                new_dx, new_dy = dir_to_dxdy(new_dir)

                pos_off = self.points().index((x, y))
                new_pos = self.other.points()[pos_off]

                return new_pos, (new_dx, new_dy)
            else:
                return (x, y), (dx, dy)
        else:
            return (x, y), (dx, dy)

    def draw(self, fb):
        for x, y in self.points():
            fb.set(x, y, hex_to_rgb(self.color))

    @property
    def linked(self):
        return self.other is not None

    def link(self, other):
        self.other = other

POWERUPS = [JumpPowerup, SpeedPowerup, PortalPowerup]

class PlayerInfo:
    COLOR_WHEEL = itertools.cycle(DOT_COLORS)

    def __init__(self, badge_id, torus=(False, False), subscriptions=None):
        self.wins = 0
        self.plays = 0
        self.maxlen = 0
        self.powerup = None

        self.badge_id = badge_id

        self.color = next(PlayerInfo.COLOR_WHEEL)

        self.torus = torus
        self.moves = 1
        self.invincible = False

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

        self.maxlen = 0

        self.powerup = None
        self.moves = 1

        # add maxlen=... to make the trails go away
        self.trail = deque()#maxlen=100 + self.wins * 6 + self.plays * 4)
        self.trail.append(initial_pos)

        self.dead = False

        self.brightness = .1

        self.moved = False

    def nom(self):
        if not self.maxlen:
            self.maxlen = len(self.trail)

        for _ in range(max(self.maxlen // 30, 1)):
            if self.trail:
                self.trail.popleft()

    def nommed(self):
        return not bool(self.trail)

    def a(self):
        if self.powerup:
            self.powerup.activate_secondary(self)

    def b(self):
        if self.powerup:
            self.powerup.activate(self)

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

    async def move(self, players, powerups, entities):
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

        if self.powerup:
            if self.powerup.activated and not self.powerup.exhausted:
                kind = self.powerup.kind

                if kind == 'Jump':
                    dx *= 4
                    dy *= 4
                elif kind == 'Portal':
                    def rot_180(d):
                        return {'u':'d','l':'r','d':'u','r':'l'}[d]

                    if self.powerup.orange_activated and not self.powerup.orange_deployed:
                        orange = Portal(self.position[0] + 10 * dx, self.position[1] + 10 * dy, self.direction, Color.ORANGE)
                        self.powerup.set_orange(orange)

                        entities.append(orange)
                    elif self.powerup.blue_activated and not self.powerup.blue_deployed:
                        blue = Portal(self.position[0] + 10 * dx, self.position[1] + 10 * dy, self.direction, Color.CYAN)
                        self.powerup.set_blue(blue)

                        entities.append(blue)

                self.powerup.tick()

            if self.powerup.exhausted:
                self.powerup.done(self)
                self.powerup = None

        x, y = self.position
        npos = ((x+dx)%WIDTH if self.torus[0] else x+dx, (y+dy)%HEIGHT if self.torus[1] else y+dy)
        nx, ny = npos

        for entity in entities:
            if entity.collide(*npos):
                if entity.kind == 'Portal':
                    npos, ndxdy = entity.calculate_path(*npos, dx, dy)
                    self.direction = dxdy_to_dir(*ndxdy)

        if not self.invincible:
            if nx < 0 or nx >= WIDTH or ny < 0 or ny >= HEIGHT or any((npos in p.trail for p in players)):
                self.dead = True
                self.brightness = 0
                return

        for powerup in powerups:
            if powerup.position == npos:
                if not self.powerup:
                    self.powerup = powerup

                powerup.consume()

        self.trail.append(npos)
        self.moved = False

    def draw(self, fb):
        for n, (x, y) in enumerate(self.trail):
            color = hex_to_rgb(self.color)

            if self.powerup and len(self.trail) - n < 10:
                color = hex_to_rgb(self.powerup.color)
            elif self.powerup and len(self.trail) - n == 10:
                color = hex_to_rgb(Color.WHITE)

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
            player.a()
        elif button == Button.B:
            player.b()
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
        self.players[badge_id] = PlayerInfo(badge_id, torus=(TORUS_H, TORUS_V), subscriptions=[press_sub])

        self.publish('badge.' + str(badge_id) + '.clear_text')
        await self.set_lights(self.players[badge_id])

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

        self.screen = flaschen.Flaschen('scootaloo.hackafe.net', 1337, WIDTH, HEIGHT, 16, True)
        self.powerups = []
        self.entities = []

        # Subscribe to all necessary things
        await self.subscribe(self.on_player_join, 'game.' + GAME_ID + '.player.join')
        await self.subscribe(self.on_player_leave, 'game.' + GAME_ID + '.player.leave')
        await self.subscribe(self.game_register, 'game.request_register')
        await self.game_register()

        while True:
            # Wait until there are two players
            while len(self.players) < 2:
                for player in self.players.values():
                    player.draw(self.screen)
                self.screen.send()
                await asyncio.sleep(.5)

            # Draw out everyone's dots for a couple seconds
            for player in self.players.values():
                player.draw(self.screen)
            self.screen.send()

            await asyncio.sleep(2)
            self.screen.clear()

            next_powerup = 0
            powerup_count = len(self.players) + 5
            # Move players until only one (or none) are left
            while sum((not p.dead for p in self.players.values())) > 1:
                # approx every 10 seconds
                if time.time() >= next_powerup:
                    next_powerup = time.time() + 5
                    for _ in range(powerup_count):
                        x, y = random.randrange(WIDTH), random.randrange(HEIGHT)
                        self.powerups.append(random.choice(POWERUPS)(x, y))
                    powerup_count = 1

                for player in self.players.values():
                    player.draw(self.screen)
                    for _ in range(player.moves):
                        await player.move(self.players.values(), self.powerups, self.entities)
                    #self.publish(target, 0, 2, "Power: " + (str(player.powerup) if player.powerup else 'None'))
                    await self.set_lights(player)

                for powerup in self.powerups:
                    powerup.draw(self.screen)

                for entity in self.entities:
                    entity.draw(self.screen)

                self.screen.send()
                self.screen.clear()

                await asyncio.sleep(.05)

            # Flash the winner's strings
            while any((not player.nommed() for player in self.players.values() if player.dead)):
                for on in (True, True, True, False, False, False, False):
                    self.screen.clear()
                    for player in list(self.players.values()):
                        if not player.dead:
                            player.brightness = .1 if on else 0

                            if on:
                                self.publish('badge.' + str(player.badge_id) + '.text', 0, 24, "You win!!!", style=1)
                                player.draw(self.screen)
                            else:
                                self.publish('badge.' + str(player.badge_id) + '.text', 0, 24, "          ", style=1)
                        else:
                            player.nom()
                            player.draw(self.screen)

                    self.screen.send()
                    await asyncio.sleep(.1 / 3)

            # Update the players' text
            for player in self.players.values():
                if not player.dead:
                    player.wins += 1

                player.plays += 1
                target = 'badge.{}.text'.format(player.badge_id)
                self.publish(target, 0, 0, "Plays: " + str(player.plays))
                self.publish(target, 0, 1, "Wins:  " + str(player.wins))


            self.screen.clear()
            self.screen.send()

            for player in self.players.values():
                player.reset()

            self.powerups = []
            self.entities = []
            

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
