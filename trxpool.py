import requests
import json
import sys
import time
import argparse
import datetime
import dateutil.parser as dp
import psycopg2


if sys.version_info[0] < 3:
	print ('python2 not supported, please use python3')
	sys.exit (0)

# Parse command line args
parser = argparse.ArgumentParser(description='TRX payments script')
parser.add_argument('-c', metavar='config.json', dest='cfile', action='store',
                   default='config.json',
                   help='set a config file (default: config.json)')
parser.add_argument('-y', dest='alwaysyes', action='store_const',
                   default=False, const=True,
                   help='automatic yes for log saving (default: no)')
parser.add_argument('--min-payout', type=float, dest='minpayout', action='store',
                   default=None,
                   help='override the minpayout value from config file')

args = parser.parse_args ()
	
# Load the config file
try:
	conf = json.load (open (args.cfile, 'r'))
except:
	print ('Unable to load config file.')
	sys.exit ()
	
if 'logfile' in conf:
	LOGFILE = conf['logfile']
else:
	LOGFILE = 'poollogs.json'

VOTERSLOG = 'voters.json'

fees = 0.0
if 'feededuct' in conf and conf['feededuct']:
	fees = conf['fees']

# Override minpayout from command line arg
if args.minpayout != None:
	conf['minpayout'] = args.minpayout


# Fix the node address if it ends with a /
if conf['node'][-1] == '/':
	conf['node'] = conf['node'][:-1]

if conf['nodepay'][-1] == '/':
	conf['nodepay'] = conf['nodepay'][:-1]


def loadLog ():
	try:
		data = json.load (open (LOGFILE, 'r'))
	except:
		print ('Unable to load log file.')
		data = {
			"lastpayout": 1529323200, 
			"accounts": {},
			"skip": []
		}
	return data

def loadVotersLog ():
	try:
		data = json.load (open (VOTERSLOG, 'r'))
	except:
		print ('Unable to load voters file.')
		data = {
			"date": 1529323200, 
			"voters": {}
		}
	return data	
	
	
def saveLog (log):
	json.dump (log, open (LOGFILE, 'w'), indent=4, separators=(',', ': '))
	
def createPaymentLine (to, amount):
	broadcast=True
	data = {"contract": {"ownerAddress": conf['owneraddress'], "toAddress": to, "assetName": conf['token'], "amount": round( amount )}, "key": conf['pk'], "broadcast": broadcast}
	nodepay = conf['nodepay']

	return 'curl -X POST "' + nodepay + '/api/transaction-builder/contract/transferasset" -H "accept: application/json" -H "Content-Type: application/json" -d \'' + json.dumps (data) + '\' ' + "\n\nsleep 1\n"

dbparam = "dbname=" + conf['dbname'] + " user=" + conf['dbuser'] + " password=" + conf['dbpass'] + " host=" + conf['dbhost']

def insertVoterInDb(voterAddress, votes, votedOn, prevDate, insertDate, toPay, snapshotNo):
    sql = """INSERT INTO voters(voterAddress, votes, votedOn, prevDate, insertDate, toPay, snapshotNo)
             values (%s, %s, %s, %s, %s, %s, %s) RETURNING id;"""
    conn = None
    id = None
    try:
    	conn = psycopg2.connect(dbparam)
    	cur = conn.cursor()
    	cur.execute(sql, (voterAddress,votes, votedOn, prevDate, insertDate, toPay, snapshotNo))
    	id = cur.fetchone()[0]
    	conn.commit()
    	cur.close()
    except (Exception, psycopg2.DatabaseError) as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()
    return id

def insertConstInDb(name, stringValue, numberValue, snapshotNo, prevDate, insertDate):
    sql = """INSERT INTO constants(name, stringValue, numberValue, snapshotNo, prevDate, insertDate)
             values (%s, %s, %s, %s, %s, %s) RETURNING id;"""
    conn = None
    id = None
    try:
    	conn = psycopg2.connect(dbparam)
    	cur = conn.cursor()
    	cur.execute(sql, (name, stringValue, numberValue, snapshotNo, prevDate, insertDate))
    	id = cur.fetchone()[0]
    	conn.commit()
    	cur.close()
    except (Exception, psycopg2.DatabaseError) as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()
    return id

def updateVoterInDb(voterAddress):
    sql = """UPDATE voters SET paid=1 where paid = NULL and voterAddress=%s RETURNING id;"""
    print (voterAddress)
    print (sql)
    conn = None
    id = None
    try:
    	conn = psycopg2.connect(dbparam)
    	cur = conn.cursor()
    	cur.execute(sql, (voterAddress))
    	id = cur.fetchone()[0]
    	conn.commit()
    	cur.close()
    except (Exception, psycopg2.DatabaseError) as error:
        #print(error + ' - ' + voterAddress)
        print(error)
    finally:
        if conn is not None:
            conn.close()
    return id

def deleteSnapshotFromDb(snapshotNo):
    sql1 = """delete from voters where snapshotno = %s RETURNING id;"""
    sql2 = """delete from constants where snapshotno = %s RETURNING id;"""
    conn = None
    id = None
    try:
    	conn = psycopg2.connect(dbparam)
    	cur = conn.cursor()
    	cur.execute(sql1, (snapshotNo))
    	id = cur.fetchone()[0]
    	cur = conn.cursor()
    	cur.execute(sql2, (snapshotNo))
    	id = cur.fetchone()[0]
    	conn.commit()
    	cur.close()
    except (Exception, psycopg2.DatabaseError) as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()
    return id

def estimatePayouts (log,voterslog):

	log['snapshotno'] = log['snapshotno'] + 1
	uri = conf['node'] + '/api/account/' + conf['sraddress']
	d = requests.get (uri)
	lf = log['lastforged']

	rew = conf ['amount']
	print ("\SHARING: %f %s" % (rew, conf['token']))
	forged=rew
	
	if forged < 0.1:
		return ([], log, 0.0)
	d = voterslog

	weight = 0.0
	payouts = []


	for x in d['data']:
		if x['votes'] == "0" or x['voterAddress'] in conf['skip']:
			continue
		if conf['private'] and not (x['voterAddress'] in conf['whitelist']):
			continue
			
		weight += float (x['votes']) 
		
	print ('WEIGHT: %f %s' % (weight, conf['coin']) + "\n")
	
	if conf['saveindb'] == True:
		insertConstInDb("FORGED", "", float (rew), log['snapshotno'], log['lastpayout'], int (time.time ()))
		insertConstInDb("WEIGHT", "", weight, log['snapshotno'], log['lastpayout'], int (time.time ()))
	for x in d['data']:
		if x['votes'] == "0" or x['voterAddress'] in conf['skip']:
			continue
			
		if conf['private'] and not (x['voterAddress'] in conf['whitelist']):
			continue
		payouts.append ({  "username": x['voterAddress'], "weight": float (x['votes']) , "address": x['voterAddress'], "balance": round((float (x['votes'])  * forged) / weight, 6), "totalweight": weight, "forged": float (rew), "votedon": dp.parse(x['timestamp']).strftime('%s') })
		if conf['saveindb'] == True:
			insertVoterInDb(x['voterAddress'], float (x['votes']), dp.parse(x['timestamp']).strftime('%s'), log['lastpayout'], int (time.time ()), round((float (x['votes'])  * forged) / weight, 6), log['snapshotno'])	
	return (payouts, log, forged)
	
	
def pool ():
	log = loadLog ()
	voterslog = loadVotersLog ()
	(topay, log, forged) = estimatePayouts (log,voterslog)
	if len (topay) == 0:
			print ('Nothing to distribute, exiting...')
			return
	f = open ('payments.sh', 'w')

	for x in topay:
		# Create the row if not present
		if not (x['address'] in log['accounts']) and x['balance'] != 0.0:
			log['accounts'][x['address']] = { 'username': x['address'], 'weight': x['weight'] / x['totalweight'] * 100, 'pending': 0.0,'received': 0.0, 'topay': 0.0, 'votedon': x['votedon'] }

		# Check if the voter has a pending balance
		pending = 0
		if x['address'] in log['accounts']:
			pending = log['accounts'][x['address']]['pending']
			
		# If below minpayout, put in the accounts pending and skip
		if (x['balance'] + pending - fees) < conf['minpayout'] and x['balance'] > 0.0:
			log['accounts'][x['address']]['pending'] += x['balance']
			log['accounts'][x['address']]['weight'] = x['weight'] / x['totalweight'] * 100
			continue
			
		# If above, update the received balance and write the payout line
		log['accounts'][x['address']]['received'] += (x['balance'] + pending)
		log['accounts'][x['address']]['weight'] = x['weight'] / x['totalweight'] * 100
		log['totalweight'] = x['totalweight']
		log['forged'] = x['forged']
		log['todistribute'] = round(x['forged'] * conf['percentage'] / 100, 6)
		if pending > 0:
			log['accounts'][x['address']]['pending'] = 0
		
		log['accounts'][x['address']]['topay'] = x['balance'] + pending - fees
		f.write ('echo Sending ' + str (x['balance'] - fees) + ' \(+' + str (pending) + ' pending\) to ' + x['address'] + '\n')
		f.write (createPaymentLine (x['address'], x['balance'] + pending - fees))
		#print ("A")
		#pdateVoterInDb(x['address'])

			
	# Handle pending balances
	for y in log['accounts']:
		# If the pending is above the minpayout, create the payout line
		if log['accounts'][y]['pending'] - fees > conf['minpayout']:
			f.write ('echo Sending pending ' + str (log['accounts'][y]['pending']) + ' to ' + y + '\n')
			f.write (createPaymentLine (y, log['accounts'][y]['pending'] - fees))
			#print ("B")
			#updateVoterInDb(y)
			
			log['accounts'][y]['received'] += log['accounts'][y]['pending']
			log['accounts'][y]['pending'] = 0.0
			

	# Donations
	if 'donations' in conf:
		for y in conf['donations']:
			f.write ('echo Sending donation ' + str (conf['donations'][y]) + ' to ' + y + '\n')
			f.write (createPaymentLine (y, conf['donations'][y]))
			#print ("C")
			#updateVoterInDb(y)


	# Donation percentage
	if 'donationspercentage' in conf:
		for y in conf['donationspercentage']:
			am = round((forged * conf['donationspercentage'][y]) / 100, 6)
			
			f.write ('echo Sending donation ' + str (conf['donationspercentage'][y]) + '% \(' + str (am) + 'TRX\) to ' + y + '\n')	
			f.write (createPaymentLine (y, am))
			#print ("D")
			#updateVoterInDb(y)

	f.close ()
	
	# Update last payout
	log['lastpayout'] = int (time.time ())
	log['totalpaid']=0
	log['totalpending']=0
	for z in log['accounts']:
		log['totalpaid']+=round(log['accounts'][z]['received'], 6)
		log['totalpending']+=round(log['accounts'][z]['pending'], 6)

	for acc in log['accounts']:
		print (acc, '\tWeight:', str(round(log['accounts'][acc]['weight'],2))+'%', '\tToPay:', log['accounts'][acc]['topay'], '\tPending:', log['accounts'][acc]['pending'], '\tVotedOn:', datetime.datetime.fromtimestamp(int(log['accounts'][acc]['votedon'])).strftime('%Y-%m-%d %H:%M:%S'))
	
	if args.alwaysyes:
		print ('Saving...')
		saveLog (log)
	else:
		yes = input ('save? y/n: ')
		if yes == 'y':
			saveLog (log)
		else:
			if conf['saveindb'] == True:
				deleteSnapshotFromDb([log['snapshotno']])

if __name__ == "__main__":
	pool ()


