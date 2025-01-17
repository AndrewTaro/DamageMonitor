API_VERSION = 'API_v1.0'
MOD_NAME = 'DamageMonitor'

from Math import Vector4
from EntityController import EntityController

try:
    import battle, events, dataHub, ui, constants, utils, callbacks
except:
    pass

CC = constants.UiComponents

def logInfo(*args):
    data = [str(i) for i in args]
    utils.logInfo( '[{}] {}'.format(MOD_NAME, ', '.join(data)) )

def logError(*args):
    data = [str(i) for i in args]
    utils.logError( '[{}] {}'.format(MOD_NAME, ', '.join(data)) )

TEAM_TOTAL_DAMAGE_COMPONENT_KEY = 'modDamageMonitorTeamTotalDamageKey'
INFLICTED_DAMAGE_COMPONENT_KEY = 'modDamageMonitorInflictedKey'
RECEIVED_DAMAGE_COMPONENT_KEY = 'modDamageMonitorReceivedKey'
TARGET_COMPONENT_KEY = 'modDamageMonitorTargetKey'

INF = float('inf')
SCALE_BASE_DAMAGE_STEP = 50000

def sortKey(d):
    return d.get('totalDamage', 0)


class TargetTracker(object):
    def __init__(self):
        self._entityController = EntityController(TARGET_COMPONENT_KEY)
        self._vary = None
        self._targetVehicleId = None

    def init(self, *rags):
        self._entityController.createEntity()
        self._targetVehicleId = None
        self._vary = callbacks.callback(0.1, self.__updateTargetVehicle)

    def kill(self, *args):
        try:
            callbacks.cancel(self._vary)
            self._vary = None
            self._targetVehicleId = None
            self._entityController.removeEntity()

        except Exception, e:
            logError('Error while killing target tracker {}'.format(e))

    def __updateTargetVehicle(self, *args, **kwargs):
        """
        Called by Vary and checks vehicle entity near the center of the screen
        """
        vehicle = battle.getObserverShip()
        if vehicle is None:
            try:
                vehicle = battle.getSelfPlayerShip()
            except:
                # observer
                pass
        vehicleId = vehicle._Ship__id if vehicle else None
        if self._targetVehicleId != vehicleId:
            targetInfo = battle.getPlayerByVehicleId(vehicleId)
            self._targetVehicleId = vehicleId
            targetAvatarId = targetInfo.id if targetInfo else None
            self._entityController.updateEntity(dict(avatarId = targetAvatarId))


class DamageMonitor(object):
    def __init__(self):
        self._inflictedDamage = {} # {attackerId: {totalDamage, {victimId: damage, ...}}, ...}
        self._receivedDamage = {}
        self._teamTotalDamage = {}

        self._targetTracker = TargetTracker()

        self._inflictedDamageEntityController = EntityController(INFLICTED_DAMAGE_COMPONENT_KEY)
        self._receivedDamageEntityController = EntityController(RECEIVED_DAMAGE_COMPONENT_KEY)
        self._teamTotalDamageEntityController = EntityController(TEAM_TOTAL_DAMAGE_COMPONENT_KEY)

        events.onBattleShown(self.init)
        events.onBattleQuit(self.kill)
        events.onReceiveDamagesOnShip(self.onDamagesUpdated)
        logInfo('Created instance')

    def init(self, *args):
        """
        Called onBattleStart
        """
        self._targetTracker.init()
        self._clearDamages()
        self._createEntities()

    def kill(self, *args):
        """
        Called onBattleQuit
        """
        try:
            self._targetTracker.kill()
            self._clearDamages()
            self._removeEntities()
        except Exception, e:
            logError('Error while killing damage monitor: {}'.format(e))

    def __updateReceivedDamage(self, attackerId, victimId, damage):
        if victimId not in self._receivedDamage:
            receivedDamage = dict(totalDamage=0, attackerIds=[])
        else:
            receivedDamage = dict(self._receivedDamage[victimId])
        receivedDamage['totalDamage'] += damage
        if attackerId not in receivedDamage:
            receivedDamage[attackerId] = 0
        receivedDamage[attackerId] += damage
        receivedDamage['attackerIds'].append(attackerId) if attackerId not in receivedDamage['attackerIds'] else None
        self._receivedDamage[victimId] = receivedDamage

    def __updateInflictedDamage(self, attackerId, victimId, damage):
        if attackerId not in self._inflictedDamage:
            playerDamage = dict(totalDamage=0, victimIds=[])
        else:
            playerDamage = dict(self._inflictedDamage[attackerId])
        # Total Damage vs All Enemies
        playerDamage['totalDamage'] += damage
        # Total Damage vs Specific Enemy
        if victimId not in playerDamage:
            playerDamage[victimId] = 0
        playerDamage[victimId] += damage
        # Vehicle IDs: Array of avatar IDs to iterate "Total Damage vs Specific Enemy" in Unbound
        # Convertion of set() to AS3 object is not supported
        # Maybe just replace this with .keys()
        playerDamage['victimIds'].append(victimId) if victimId not in playerDamage['victimIds'] else None

        self._inflictedDamage[attackerId] = playerDamage

    def __updateTeamDamage(self, attackerTeamId, damage):
        if attackerTeamId not in self._teamTotalDamage:
            self._teamTotalDamage[attackerTeamId] = 0
        self._teamTotalDamage[attackerTeamId] += damage

    def __updateDamageScaleBase(self):
        maxTotalInflictedDamage = max(self._inflictedDamage.itervalues(), key=sortKey)['totalDamage'] if len(self._inflictedDamage) > 0 else 0
        maxTotalReceivedDamage = max(self._receivedDamage.itervalues(),   key=sortKey)['totalDamage'] if len(self._receivedDamage)  > 0 else 0
        maxDamage = max(maxTotalInflictedDamage, maxTotalReceivedDamage)
        # x1.12: Slight offset because the damage bar shouldnt be "100%"
        self._teamTotalDamage['personalDamageScaleBase'] = ((maxDamage * 1.12 // SCALE_BASE_DAMAGE_STEP) + 1) * SCALE_BASE_DAMAGE_STEP

    def onDamagesUpdated(self, victimId, damages):
        """
        Update internal dicts
        
        :param victimId: Entity ID of victim vehicle
        :type  victimId: int
        :param damages: Damages
        :type  damages: List[ Dict[vehicleID, damage] ]
        """
        # Using avatarId because attacker vehicles can be undetected
        # and vehicle component can be None (so unbound dh cannot find corresponding player)
        attackers = set()
        victim = battle.getPlayerByVehicleId(victimId)
        if victim is None:
            # There should Never be a vehicle without avatar right? but just in case
            logError('Victim player does not exist. vehId: {}'.format(victimId))
            return
        victimId = victim.id

        for damageData in damages:
            damage = round(damageData['damage'])
            if damage <= 0:
                # Do nothing if damage is 0 or negative.
                # This includes frinedly fires
                #
                # may cause a problem with "heal" shells
                # but that was a halloween event years ago and shouldn't happen in normal games
                continue

            attacker = battle.getPlayerByVehicleId(damageData['vehicleID'])
            attackerId = attacker.id

            attackers.add(attackerId)

            # Team Total Damage
            self.__updateTeamDamage(attacker.teamId, damage)

            # Personal Damages
            self.__updateInflictedDamage(attackerId, victimId, damage)
            self.__updateReceivedDamage(attackerId, victimId, damage)

        # Sorting avatarId by damage
        # Higher damage means smaller index
        for attackerId in attackers:
            inflictedDamage = self._inflictedDamage[attackerId]
            inflictedDamage['victimIds'].sort(key = lambda x: inflictedDamage[x], reverse=True)
        
        if victimId in self._receivedDamage:
            # victimId may not exist if the damage is 0 and therefore skipped
            receivedDamage = self._receivedDamage[victimId]
            receivedDamage['attackerIds'].sort(key = lambda x: receivedDamage[x], reverse=True)

        # For total damage bar scaling
        self.__updateDamageScaleBase()

        # Update DH
        self._updateEntities()

    def _clearDamages(self):
        """
        Clear internal dicts
        """
        self._inflictedDamage.clear()
        self._receivedDamage.clear()
        self._teamTotalDamage.clear()

    def _createEntities(self):
        """
        Create datahub entities
        """
        self._teamTotalDamageEntityController.createEntity()
        self._inflictedDamageEntityController.createEntity()
        self._receivedDamageEntityController.createEntity()

    def _removeEntities(self):
        """
        Remove datahub entities
        """
        self._teamTotalDamageEntityController.removeEntity()
        self._inflictedDamageEntityController.removeEntity()
        self._receivedDamageEntityController.removeEntity()

    def _updateEntities(self):
        """
        Update datahub entities
        """
        self._inflictedDamageEntityController.updateEntity(self._inflictedDamage)
        self._receivedDamageEntityController.updateEntity(self._receivedDamage)
        self._teamTotalDamageEntityController.updateEntity(self._teamTotalDamage)


gDamageMonitor = DamageMonitor()