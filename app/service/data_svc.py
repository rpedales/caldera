import glob
from base64 import b64encode
from collections import defaultdict
from datetime import datetime


class DataService:

    def __init__(self, dao, utility_svc):
        self.dao = dao
        self.utility_svc = utility_svc
        self.log = utility_svc.create_logger('data_svc')

    async def reload_database(self, schema='conf/core.sql', adversaries=None, abilities=None, facts=None, planner=None):
        with open(schema) as schema:
            await self.dao.build(schema.read())
        await self.load_abilities(directory=abilities)
        await self.load_adversaries(directory=adversaries)
        await self.load_facts(config=facts)
        await self.load_planner(planner)

    async def load_adversaries(self, directory):
        for filename in glob.iglob('%s/**/*.yml' % directory, recursive=True):
            for entries in self.utility_svc.strip_yml(filename):
                for adv in entries:
                    phases = [dict(phase=k, id=i) for k, v in adv['phases'].items() for i in v]
                    await self.create_adversary(adv['id'], adv['name'], adv['description'], phases)

    async def load_abilities(self, directory):
        for filename in glob.iglob('%s/**/*.yml' % directory, recursive=True):
            for entries in self.utility_svc.strip_yml(filename):
                for ab in entries:
                    for ex, el in ab['executors'].items():
                        encoded_test = b64encode(el['command'].strip().encode('utf-8'))
                        await self.create_ability(ability_id=ab.get('id'), tactic=ab['tactic'],
                                                  technique=ab['technique'], name=ab['name'],
                                                  test=encoded_test.decode(), description=ab.get('description'),
                                                  platform=ex,
                                                  cleanup=b64encode(
                                                      el['cleanup'].strip().encode('utf-8')).decode() if el.get(
                                                      'cleanup') else None,
                                                  payload=el.get('payload'), parser=el.get('parser'))

    async def load_facts(self, config):
        for entries in self.utility_svc.strip_yml(config):
            for facts in entries:
                source_id = await self.dao.create('core_source', dict(name=facts['name']))
                for fact in facts['facts']:
                    fact['source_id'] = source_id
                    await self.create_fact(**fact)

    async def load_planner(self, planner):
        if planner:
            await self.dao.create('core_planner', dict(name=planner.get('name'), module=planner.get('module')))

    """ CREATE """

    async def create_ability(self, ability_id, tactic, technique, name, test, description, platform, cleanup=None,
                             payload=None, parser=None):
        await self.dao.create('core_attack',
                              dict(attack_id=technique['attack_id'], name=technique['name'], tactic=tactic))
        entry = await self.dao.get('core_attack', dict(attack_id=technique['attack_id']))
        entry_id = entry[0]['attack_id']
        identifier = await self.dao.create('core_ability',
                                           dict(ability_id=ability_id, name=name, test=test, technique=entry_id,
                                                platform=platform, description=description, cleanup=cleanup))
        if payload:
            await self.dao.create('core_payload', dict(ability=identifier, payload=payload))
        if parser:
            parser['ability'] = identifier
            await self.dao.create('core_parser', parser)
        return identifier

    async def create_adversary(self, i, name, description, phases):
        identifier = await self.dao.create('core_adversary', dict(adversary_id=i, name=name.lower(), description=description))
        await self.dao.delete('core_adversary_map', dict(adversary_id=identifier))
        for ability in phases:
            a = (dict(adversary_id=identifier, phase=ability['phase'], ability_id=ability['id']))
            await self.dao.create('core_adversary_map', a)
        return identifier

    async def create_group(self, name, paws):
        identifier = await self.dao.create('core_group', dict(name=name))
        for paw in paws:
            agent = await self.dao.get('core_agent', dict(paw=paw))
            await self.dao.create('core_group_map', dict(group_id=identifier, agent_id=agent[0]['id']))
        return identifier

    async def create_operation(self, name, group, adversary_id, jitter='2/8', cleanup=True, stealth=False,
                               sources=None, planner=None):
        op_id = await self.dao.create('core_operation', dict(
            name=name, host_group=group, adversary_id=adversary_id, finish=None, phase=0, jitter=jitter,
            start=datetime.now().strftime('%Y-%m-%d %H:%M:%S'), cleanup=cleanup, stealth=stealth, planner=planner)
                                      )
        source_id = await self.dao.create('core_source', dict(name=name))
        await self.dao.create('core_source_map', dict(op_id=op_id, source_id=source_id))
        for s_id in [s for s in sources if s]:
            await self.dao.create('core_source_map', dict(op_id=op_id, source_id=s_id))
        return op_id

    async def create_fact(self, property, value, source_id, score=1, blacklist=0, set_id=0, link_id=None):
        await self.dao.create('core_fact', dict(property=property, value=value, source_id=source_id,
                                                score=score, blacklist=blacklist, set_id=set_id, link_id=link_id))

    async def create_link(self, link):
        await self.dao.create('core_chain', link)

    async def create_result(self, result):
        await self.dao.create('core_result', result)

    async def create_agent(self, agent):
        await self.dao.create('core_agent', agent)

    """ VIEW """

    async def explode_abilities(self, criteria=None):
        abilities = await self.dao.get('core_ability', criteria=criteria)
        for ab in abilities:
            ab['cleanup'] = '' if ab['cleanup'] is None else ab['cleanup']
            ab['parser'] = await self.dao.get('core_parser', dict(ability=ab['id']))
            ab['payload'] = await self.dao.get('core_payload', dict(ability=ab['id']))
            ab['technique'] = (await self.dao.get('core_attack', dict(attack_id=ab['technique'])))[0]
        return abilities

    async def explode_adversaries(self, criteria=None):
        adversaries = await self.dao.get('core_adversary', criteria)
        for adv in adversaries:
            phases = defaultdict(list)
            for t in await self.dao.get('core_adversary_map', dict(adversary_id=adv['id'])):
                for ability in await self.explode_abilities(dict(ability_id=t['ability_id'])):
                    phases[t['phase']].append(ability)
            adv['phases'] = dict(phases)
        return adversaries

    async def explode_operation(self, criteria=None):
        operations = await self.dao.get('core_operation', criteria)
        for op in operations:
            op['chain'] = sorted(await self.explode_chain(criteria=dict(op_id=op['id'])), key=lambda k: k['id'])
            groups = await self.explode_groups(dict(id=op['host_group']))
            op['host_group'] = groups[0]
            adversaries = await self.explode_adversaries(dict(id=op['adversary_id']))
            op['adversary'] = adversaries[0]
            sources = await self.dao.get('core_source_map', dict(op_id=op['id']))
            op['facts'] = await self.dao.get_in('core_fact', 'source_id', [s['source_id'] for s in sources])
        return operations

    async def explode_groups(self, criteria=None):
        groups = await self.dao.get('core_group', criteria=criteria)
        for g in groups:
            g['agents'] = await self.dao.get('core_group_map', dict(group_id=g['id']))
        return groups

    async def explode_agents(self, criteria: object = None) -> object:
        agents = await self.dao.get('core_agent', criteria)
        groups = await self.dao.get('core_group', criteria=dict(deactivated=0))
        for g in groups:
            for gm in await self.dao.get('core_group_map', criteria=dict(group_id=g['id'])):
                g['agent_id'] = gm['agent_id']
                g['map_id'] = gm['id']
        for a in agents:
            a['groups'] = [dict(id=g['id'], name=g['name'], map_id=g['map_id']) for g in groups if g['agent_id'] == a['id']]
        return agents

    async def explode_results(self, criteria=None):
        results = await self.dao.get('core_result', criteria=criteria)
        for r in results:
            link = await self.dao.get('core_chain', dict(id=r['link_id']))
            link[0]['facts'] = await self.dao.get('core_fact', dict(link_id=link[0]['id']))
            r['link'] = link[0]
        return results

    async def explode_chain(self, criteria=None):
        chain = []
        for link in await self.dao.get('core_chain', criteria=criteria):
            a = await self.dao.get('core_ability', criteria=dict(id=link['ability']))
            chain.append(dict(abilityName=a[0]['name'], abilityDescription=a[0]['description'], **link))
        return chain

    async def explode_sources(self, criteria=None):
        sources = await self.dao.get('core_source', criteria=criteria)
        for s in sources:
            s['facts'] = await self.dao.get('core_fact', dict(source_id=s['id']))
        return sources

    async def explode_planners(self, criteria=None):
        return await self.dao.get('core_planner', criteria=criteria)

    async def explode_payloads(self, criteria=None):
        return await self.dao.get('core_payload', criteria=criteria)

    async def explode_parsers(self, criteria=None):
        return await self.dao.get('core_parser', criteria=criteria)

    """ DELETE / DEACTIVATE """

    async def delete(self, index, id):
        if index == 'core_group':
            return await self.deactivate_group(id)
        if index == 'core_agent':
            await self.dao.delete('core_group_map', data=dict(agent_id=id))    
        await self.dao.delete(index, data=dict(id=id))
        return 'Removed %s from %s' % (id, index)
        
    async def deactivate_group(self, group_id):
        group = await self.dao.get('core_group', dict(id=group_id))
        await self.dao.update(table='core_group', key='id', value=group_id,
                              data=dict(deactivated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        return 'Removed %s host group' % group[0]['name']

    """ UPDATE """

    async def update(self, table, key, value, data):
        await self.dao.update(table, key, value, data)

