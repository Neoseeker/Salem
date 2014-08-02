import logging, re
log = logging.getLogger(__name__)

from datetime import datetime
from classes.neoraffle import neoraffle, UserAlreadyRegistered, MultipleValidationErrors, DoesNotExist, UserNotRegistered, InvalidAuctionType, UserCannotAffordItem, BidDoesNotExceedCurrentTopBid, UserAttemptToPurchaseOwnItem, UserAccountIsInactive

class raffleplugin():
	MAXBONUS = 4 # Maximum number of items a user can earn bonus points for offering.
	BONUSPTS = 250 # Number of bonus points given for each item offered in the raffle/auction.
	
	def __init__(self, salemhook, neohook):
		self.salem = salemhook
		self.neo = neohook
		self.raffle = neoraffle()
		
	def notificationHandler(self, apiPostInfo, apiMemberInfo):
		curRafflePhase = self.salem.getSalemConfig("NEORAFFLE_PHASE")
		
		if apiPostInfo['thread']['threadid'] == self.salem.getSalemConfig("NEORAFFLE_THREAD"): # Only catch notifs from defined thread.
			if curRafflePhase == "userreg" and "NEORAFFLE REGISTER" in apiPostInfo['body']:
				self.__registration(apiMemberInfo, apiPostInfo)
			if curRafflePhase == "itemreg" and "NEORAFFLE ITEM ADD" in apiPostInfo['body']:
				self.__itemaddition(apiMemberInfo, apiPostInfo)
			if curRafflePhase == "bidding" and "NEORAFFLE PURCHASE" in apiPostInfo['body']:
				self.__purchasing(apiMemberInfo, apiPostInfo)
			if curRafflePhase == "bidding" and "NEORAFFLE BID" in apiPostInfo['body']:
				self.__purchasing(apiMemberInfo, apiPostInfo, shortMethod=True)
			if curRafflePhase == "bidding" and "NEORAFFLE BUY" in apiPostInfo['body']:
				self.__purchasing(apiMemberInfo, apiPostInfo, shortMethod=True)
			if curRafflePhase == "itemreg" and "NEORAFFLE DELETE" in apiPostInfo['body']:
				self.__userdeleteitem(apiMemberInfo, apiPostInfo)
	
	def ircHandler(self, irctarget, ircsource, ircmsg):
		try:
			if ircmsg[0] == "@neoraffle":
				if ircmsg[1] == "phase":
					self.__changePhase(irctarget, ircmsg)
				elif ircmsg[1] == "thread":
					self.__configRaffleThread(irctarget, ircmsg)
				elif ircmsg[1] == "delete":
					self.__deleteItem(irctarget, ircmsg)
				elif ircmsg[1] == "edit":
					self.__editItem(irctarget, ircmsg)
				elif ircmsg[1] == "currency":
					self.__usercurrency(irctarget, ircmsg)
				else:
					self.salem.send_message(irctarget, "** [06NeoRaffle] Invalid option! Available options: currency <user> [newcurrency], thread <id>, phase <off/userreg/itemreg/bidding/winners>, delete <id>, edit <id> <params>")
		except IndexError:
			self.salem.send_message(irctarget, "** [06NeoRaffle] Initilized database successfully. Available options: currency <user> [newcurrency], thread <id>, phase <off/userreg/itemreg/bidding/winners>, delete <id>, edit <id> <params>")
		except:
			log.exception("Unknown error from IRC command.")
			self.salem.send_message(irctarget, "** [06NeoRaffle] Unknown error occurred in NeoRaffle IRC handler.")
		
	# Notification processes:
	def __registration(self, apiMemberInfo, apiPostInfo):
		notifyUser = self.neo.getForumNotifyStringForUsername(apiMemberInfo['username'])
		
		try:
			res = self.raffle.handleNeoraffleRegistration(apiMemberInfo['memberid'], apiMemberInfo['username'], \
											apiMemberInfo['neopoints'], apiMemberInfo['gamegreppoints'], apiMemberInfo['forum_msgs_count'], apiMemberInfo['wikiedits_count'])
		except UserAlreadyRegistered:
			output = "Hi {0}.\n\nI detected you're trying to register in your post ({1}), but we already have a record for you in the Neo Raffle DB. You are already registered and your account is ready to participate. :)".format(notifyUser, apiPostInfo['messageid'])
			self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Registration: Already Registered!", output)
			return
		except:
			output = "Hi {0}\n\nAn unknown error occurred when attempting to register your account from post: {1}.  Sorry. :(\n\n@Dynamite should fix me!".format(notifyUser, apiPostInfo['messageid'])
			self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Registration: Error!", output)
			log.exception("Unknown error from NeoRaffle user registration handler!")
			return
		
		if res: # Registration was successful and returned the dictionary with the user's currency breakdown.
			output = "NeoRaffle registration successful for: {0}! Your total available currency is: [color=red][b]{1}[/b][/color].\n\nCurrency breakdown:\n".format(notifyUser, res['totalpts'])
			output += "[ul]\n"
			output += "[li][b]NeoPoints[/b]: {0}\n".format(res['neopts'])
			output += "[li][b]GameGrep Points[/b]: {0}\n".format(res['ggpts'])
			output += "[li][b]Post Points[/b]: {0}\n".format(res['postpts'])
			output += "[li][b]Wiki Points[/b]: {0}\n".format(res['wikipts'])
			output += "[/ul]"
			
			self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Registration for {0}".format(apiMemberInfo['username']), output)
			return True
	
	def __itemaddition(self, apiMemberInfo, apiPostInfo):
		notifyUser = self.neo.getForumNotifyStringForUsername(apiMemberInfo['username'])
		postbody = apiPostInfo['body'].encode('ascii', errors='ignore')
		log.debug("Post body received for NeoRaffle item addition: {0}".format(postbody))
		
		# Check if user is registered first.  If they are not, create an inactive user account for them:
		if not self.raffle.isUserRegistered(apiMemberInfo['memberid']):
			self.raffle.handleNeoraffleRegistration(apiMemberInfo['memberid'], apiMemberInfo['username'], \
											   apiMemberInfo['neopoints'], apiMemberInfo['gamegreppoints'], \
											   apiMemberInfo['forum_msgs_count'], apiMemberInfo['wikiedits_count'], isactive=False)
		
		extractedForms = []
		
		# Regex for form and individual item acquisition:
		raffleFormRegex = re.compile("\[b\]RAFFLE ITEM\[\/b\].+?Quantity:\s?[^\n]+\n?", re.DOTALL)		
		raffleItemRegex = re.compile("\[b\](?P<type>RAFFLE) ITEM\[\/b\].+?Item Title:\s?(?P<title>.+?)\nItem Description:\s?(?P<description>.+?)Ticket Price:\s?(?P<price>.+?)Quantity:\s?(?P<quantity>[^\n]+)", re.DOTALL)
		
		# Auction items:
		auctionFormRegex = re.compile("\[b\]AUCTION ITEM\[\/b\].+?Quantity:\s?[^\n]+\n?", re.DOTALL)
		auctionItemRegex = re.compile("\[b\](?P<type>AUCTION) ITEM\[\/b\].+?Item Title:\s?(?P<title>.+?)\nItem Description:\s?(?P<description>.+?)Quantity:\s?(?P<quantity>[^\n]+)", re.DOTALL)
		
		# Find all instances of raffle item forms in the post and return each as a list element:
		raffleForms = raffleFormRegex.findall(postbody)
		log.debug("Raffle forms found: {0}".format(raffleForms))
		
		# ... and auction forms:
		auctionForms = auctionFormRegex.findall(postbody)
		log.debug("Auction forms found: {0}".format(auctionForms))
		
		if not raffleForms and not auctionForms: # User has requested item addition, but no forms were found in the post.
			output = "Hi {0}.\n\nI was unable to find any valid forms in your post ({1}). Please ensure you copy/paste the code for the form exactly and do not modify it. You should also ensure you use numeric values where appropriate.".format(notifyUser, apiPostInfo['messageid'])
			log.error("User {0} requested NeoRaffle item addition from post {1}, but no valid forms were found!".format(apiMemberInfo['username'], apiPostInfo['messageid']))
			self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Item Addition: Error", output)
			return
	
		# Now to extract each element in the form:
		for form in raffleForms:
			itemMatch = raffleItemRegex.search(form) # G1: type, G2: title, G3: description, G4: price, G5: quantity.
			extractedForms.append(itemMatch.groupdict()) # Add the match dict to the list.
		
		for form in auctionForms:
			itemMatch = auctionItemRegex.search(form) # G1: type, G2: title, G3: description, G4: quantity
			extractedForms.append(itemMatch.groupdict())
		
		output = "Hi {0}.  I'm processing the following forms from your post ({1}):\n\n".format(notifyUser, apiPostInfo['messageid'])
		for i, extractedData in enumerate(extractedForms):
			output += "[b][u]Form: {0} ({1})[/u][/b]\n\n".format(i+1, extractedData['type'])
			
			if extractedData['type'] == "RAFFLE":
				listtype = 1
			elif extractedData['type'] == "AUCTION":
				listtype = 2
				extractedData['price'] = None # Price isn't specified in auctions, set it to null for the DB call.
			
			try:				
				htmltitle = self.neo.translateMarkupToHtml(extractedData['title'])
				htmlbody = self.neo.translateMarkupToHtml(extractedData['description'])
				
				res = self.raffle.addItemToDatabase(apiMemberInfo['memberid'], extractedData['title'], extractedData['description'], \
											extractedData['price'], extractedData['quantity'], itemtype=listtype, htmltitle=htmltitle, htmldescription=htmlbody)
				
				tnum = self.raffle.getNumOwnedItems(apiMemberInfo['memberid'])
				
				if res:
					output += "[color=green][b]Item was successfully added as a [i]{0}[/i] lot![/b][/color]\n\n".format("raffle" if listtype == 1 else "auction")
					output += "[size=4][b]Lot Number: [color=red]{0}[/color][/b][/size]\n\n[ul]".format(res)
					output += "[li][b]Item[/b]: {0}".format(extractedData['title'])
					output += "[li][b]Description[/b]: {0}".format(extractedData['description'])
					if listtype == 1:
						output += "[li][b]Price[/b]: {0}".format(extractedData['price'])
					output += "[li][b]Quantity[/b]: {0}".format(extractedData['quantity'])
					output += "[/ul]\n\n"
					
					# Add a bonus of +250 points to user up to the first 4 items added (max 1,000):
					if tnum <= raffleplugin.MAXBONUS:						
						self.raffle.setUserAvailableCurrency(apiMemberInfo['memberid'], delta=raffleplugin.BONUSPTS)
						
						output += "[b]Note[/b]: You have been credited with +[b]{}[/b] bonus points for offering an item. You've earned [b]{}[/b] of a maximum [b]{}[/b] bonuses for offering items.\n\n".format(raffleplugin.BONUSPTS, raffleplugin.BONUSPTS*tnum, raffleplugin.BONUSPTS*raffleplugin.MAXBONUS)
			except MultipleValidationErrors as e:
				output += "There was a problem processing the form from your post ({0}).  The following errors were detected:\n\n[ul]".format(apiPostInfo['messageid'])
				for err in e:
					output += "[li] {0}".format(err)
				output += "[/ul]\n\n"
			except:
				output += "Fatal error attempting to add item from post {0}. :(  @Dynamite should fix me.".format(apiPostInfo['messageid'])
				log.exception("An error occurred when attempting to add an item to the auction database!")
			
		if output:
			self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Item Addition for {0}".format(apiMemberInfo['username']), output)
			
	def __purchasing(self, apiMemberInfo, apiPostInfo, shortMethod=False):
		notifyUser = self.neo.getForumNotifyStringForUsername(apiMemberInfo['username'])
		postbody = apiPostInfo['body'].encode('ascii', errors='ignore')
				
		extractedBids = []

		buyRegex = re.compile("(?P<type>Buy):?\s?#?(?P<item>\d+?) (?P<quantity>[0-9,]+)", re.IGNORECASE) # ItemID, Quantity
		bidRegex = re.compile("(?P<type>Bid):?\s?#?(?P<item>\d+?) (?P<bid>[0-9,]+)", re.IGNORECASE) # ItemID, Bid (Pts)
		
		# Find all instances of bids in the post and return each as a list element:
		raffleBids = buyRegex.findall(postbody)
		auctionBids = bidRegex.findall(postbody)
		
		# Added for convenience for single purchases:
		if shortMethod is True:
			buyShortRegex = re.compile("NEORAFFLE BUY (?P<item>\d+?) (?P<quantity>[0-9,]+)")
			bidShortRegex = re.compile("NEORAFFLE BID (?P<item>\d+?) (?P<bid>[0-9,]+)")
			
			shortBuys = buyShortRegex.search(postbody)
			shortBids = bidShortRegex.search(postbody)
			
			try:
				if shortBuys:
					raffleBids.append(["Buy", shortBuys.group('item'), shortBuys.group('quantity')])
				if shortBids:
					auctionBids.append(["Bid", shortBids.group('item'), shortBids.group('bid')])
			except (AttributeError, IndexError):
				pass # There were inconsistencies with the group names/data extracted, just let it pass and it'll be handled below.
				
		
		if not raffleBids and not auctionBids: # No bids found:
			output = "Hi {0}.\n\nI was unable to find any bids in your post ({1}). Please check the first post again and ensure you use the correct format!".format(notifyUser, apiPostInfo['messageid'])
			log.error("User {0} requested NeoRaffle bid post {1}, but no valid bids were found!".format(apiMemberInfo['username'], apiPostInfo['messageid']))
			self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Bid: Error", output)
			return
		
		extractedBids = raffleBids + auctionBids
			
		output = "Hi {0}.  I'm processing the following bids from your post ({1}):\n\n".format(notifyUser, apiPostInfo['messageid'])
		for i, extractedData in enumerate(extractedBids):
			rtn = None
			output += "[b][u]Purchase {0} ({1} on lot {2})[/u][/b]\n\n".format(i+1, extractedData[0], extractedData[1])
			
			try:
				if extractedData[0].upper() == "BID":
					rtn = self.raffle.makePurchase("auction", apiMemberInfo['memberid'], extractedData[1], bid=extractedData[2])  
				elif extractedData[0].upper() == "BUY":
					rtn = self.raffle.makePurchase("raffle", apiMemberInfo['memberid'], extractedData[1], quantity=extractedData[2])
			except UserNotRegistered:
				output = "[color=red][b]Error[/b][/color]: Unfortunately, {0}, you do not appear to be registered with the NeoRaffle system. You may only bid on items if you registered during stage 1 of the annual raffle event.".format(notifyUser)
				self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Bid: Error", output)
				return
			except UserAccountIsInactive:
				output += "[color=red][b]Error[/b][/color]: {0}, your user account is not eligible to participate in purchasing. You must have registered with the NeoRaffle system during phase 1 in order to be able to purchase items. ".format(notifyUser)
				self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Bid: Error", output)
				return
			except DoesNotExist:
				output += "[color=red][b]Error[/b][/color]: The lot number you specified ({0}) was not found in the items database! Please check and try again.".format(extractedData[1])
			except ValueError as e:
				output += "[color=red][b]Error[/b][/color]: An error occurred when attempting to place your bid. Please ensure you use the correct format and values for bids and try again."
				log.error("Error when purchasing.  Error was: {0}".format(e))
			except UserCannotAffordItem as e:
				output += "[color=red][b]Error[/b][/color]: You cannot afford to make this purchase! {0}".format(e)
			except InvalidAuctionType:
				output += "[color=red][b]Error[/b][/color]: You appear to be trying to bid on a raffle item or buy an auction item. Please use 'Buy:' for raffle items and 'Bid:' for auction items"
			except BidDoesNotExceedCurrentTopBid as e:
				output += "[color=red][b]Error[/b][/color]: {0}".format(e)
			except UserAttemptToPurchaseOwnItem as e:
				output += "[color=red][b]Error[/b][/color]: {0}".format(e)
			except:
				log.exception("Unknown error when attempting to process log purchase")
				output += "[color=red][b]Error[/b]: An unknown error occurred when attempting to record your purchase. @Dynamite should fix me. :("
				
			if rtn: # Purchase was successful:
				prevbiddernotify = None
				
				if extractedData[0].upper() == "BID":
					try:
						if rtn['prevtopbidder']['userid']:
							prevbiddernotify = self.neo.getForumNotifyStringForUsername(self.neo.getMemberIdFromUsernameOrId(rtn['prevtopbidder']['userid']))
							prevtopbid = rtn['prevtopbidder']['amount']
					except:
						log.exception("Error occurred when attempting to get previous bidder to notify!")
						prevbiddernotify = "The previous top bidder"
						prevtopbid = "[i]unknown[/i]"
					
					output += "[color=green][b]Auction Bid Successful![/b][/color] Your bid for lot {} ([http://raffle.pwnsu.com/items/{} {}]) was accepted!  Your bid of [b]{}[/b] makes you the current highest bidder!".format(extractedData[1], rtn['iteminfo']['lotnum'], rtn['iteminfo']['title'], extractedData[2])
					
					if prevbiddernotify:
						output += "\n\n[color=red][b]ALERT[/b][/color]: {0} has been outbid for this lot! The previous top bid was: [b]{1}[/b]".format(prevbiddernotify, prevtopbid)
				elif extractedData[0].upper() == "BUY":
					output += "[color=green][b]Raffle Purchase Successful![/b][/color] You have successfully bought [b]{}[/b] tickets for lot {} ([http://raffle.pwnsu.com/items/{}/ {}]) at the cost of [b]{}[/b] per ticket, totalling [b]{}[/b].".format(extractedData[2], extractedData[2], rtn['iteminfo']['lotnum'], rtn['iteminfo']['title'], rtn['costinfo']['ticketprice'], rtn['costinfo']['totalcost'])
			
			output += "\n\n"
		output += "You have [color=red][b]{0}[/b][/color] points remaining.".format(self.raffle.getUserAvailableCurrency(apiMemberInfo['memberid']))
		self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Purchase", output)
		
	def __userdeleteitem(self, apiMemberInfo, apiPostInfo):	 
		notifyUser = self.neo.getForumNotifyStringForUsername(apiMemberInfo['username'])
		postbody = apiPostInfo['body'].encode('ascii', errors='ignore')
				
		deletions = ""
		delRegex = re.compile("NEORAFFLE DELETE (?P<item>[0-9, ]+)") # Lot ID CSV.
		
		# Find all instances of bids in the post and return each as a list element:
		deletions = delRegex.search(postbody)
		
		if not deletions: # No deletions found.
			output = "Hi {0}.\n\nI was unable to find any specified items to delete in your post ({1}). Please check the first post again and ensure you use the correct format!".format(notifyUser, apiPostInfo['messageid'])
			self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Deletion: Error", output)
			return
		
		deletions = deletions.group('item')
		deletions.replace(' ','')
		deletions = deletions.split(',')
			
		output = "Hi {0}.  I'm processing the following deletion requests from your post ({1}):\n\n[ul]".format(notifyUser, apiPostInfo['messageid'])
		for deletion in deletions:
			try:
				# Get current number of items before deletion:
				numitems = self.raffle.getNumOwnedItems(apiMemberInfo['memberid'])
				
				# Delete item:
				self.raffle.deleteItem(deletion, apiMemberInfo['memberid'])
				output += "[li] Item {0} was successfully deleted!".format(deletion)
				
				# Remove bonus points given for adding items which were awarded them
				if numitems <= raffleplugin.MAXBONUS: 
					self.raffle.setUserAvailableCurrency(apiMemberInfo['memberid'], delta=raffleplugin.BONUSPTS - raffleplugin.BONUSPTS*2)
					output += " Note: {} bonus points removed for adding this item!".format(raffleplugin.BONUSPTS)
			except DoesNotExist:
				output += "[li] Item {0} was not found in the DB!".format(deletion)
			except UserNotRegistered:
				output += "[li] Sorry, you do not appear to be registered with the NeoRaffle system."
				break
			except ValueError:
				output += "[li] Item {0} doesn't belong to you! You can't delete it!".format(deletion)
		
		output += "[/ul]"	
		self.neo.postToForums(apiPostInfo['thread']['threadid'], "NeoRaffle Deletion", output)
	
	# IRC command processes:
	def __configRaffleThread(self, channel, ircmsg):
		try:
			thread = int(ircmsg[2])
		except IndexError:
			self.salem.send_message(channel, "** [06NeoRaffle] Current thread is: {0}".format(self.salem.getSalemConfig('NEORAFFLE_THREAD')))
			return
		except ValueError:
			self.salem.send_message(channel, "** [06NeoRaffle] Couldn't set thread! Please specify the numeric thread ID only.")
			return
		
		self.salem.setSalemConfig("NEORAFFLE_THREAD", str(thread))
		self.salem.send_message(channel, "** [06NeoRaffle] Raffle thread has been set to: {0}".format(thread))
	
	def __changePhase(self, channel, ircmsg):
		thread = self.salem.getSalemConfig("NEORAFFLE_THREAD")
		curphase = self.salem.getSalemConfig("NEORAFFLE_PHASE")
		
		raffleusers = self.raffle.fetchRegisteredUsers()
		
		# Build notification box:
		notifybox = "[spoiler=Notification for Raffle Users]"
		
		for user in raffleusers:
			notifybox += "{0} ".format(self.neo.getForumNotifyStringForUsername(user))
		notifybox += "[/spoiler]"
			
		try:
			newphase = ircmsg[2]
		except IndexError:
			self.salem.send_message(channel, "** [06NeoRaffle] Current phase is: {0}".format(self.salem.getSalemConfig('NEORAFFLE_PHASE')))
			return
		
		if not thread:
			self.salem.send_message(channel, "** [06NeoRaffle] Please specify the NeoRaffle thread ID with @neoraffle thread <id> before changing phases!")
			return
		
		if newphase == curphase:
			self.salem.send_message(channel, "** [06NeoRaffle] The raffle is already in the {0} phase!".format(curphase))
			return		
		
		# Phase sets begin:	
		if newphase == "off":
			self.salem.setSalemConfig("NEORAFFLE_PHASE", "off")
			
			posttopic = "NeoRaffle Disabled!"
			
			output = "The NeoRaffle system has been disabled at [date]{0}[/date]!\n\nWe're not presently \
			processing NeoRaffle operations at this time.".format(datetime.strftime(datetime.now(),'%Y-%m-%d %H:%M:%S'))
		
		elif newphase == "userreg":
			self.salem.setSalemConfig("NEORAFFLE_PHASE", "userreg")
			
			posttopic = "Now Accepting User Registrations!"
			
			output = "The NeoRaffle user registration phase has begun at [date]{0}[/date]!\n\nDuring this phase of the raffle, \
			users will be able to register with the system using the command in the opening post. Registering \
			will record your available points total to spend in the raffle from a combination of your post count, \
			NeoPoints, GameGrep points and NeoWiki edits.\n\nUser registration will close at the date specified in the opening \
			post. Please note that users who have not registered before the deadline [b]will not[/b] be eligible \
			to participate in this year's raffle!".format(datetime.strftime(datetime.now(),'%Y-%m-%d %H:%M:%S'))
			
		elif newphase == "itemreg":
			self.salem.setSalemConfig("NEORAFFLE_PHASE", "itemreg")
			
			posttopic = "Now Accepting Item Registrations!"
			
			output = "The NeoRaffle item registration phase has begun at [date]{0}[/date]!\n\nDuring this phase, users are \
			free to register items they wish to put up for raffle or auction following the guidelines within the opening \
			post.\n\n{1}".format(datetime.strftime(datetime.now(),'%Y-%m-%d %H:%M:%S'), notifybox)
			
	
		elif newphase == "bidding":
			self.salem.setSalemConfig("NEORAFFLE_PHASE", "bidding")
			
			posttopic = "Now Accepting Bids!"
			
			output = "The NeoRaffle bidding phase has begun at [date]{0}[/date]!\n\nDuring this time, you are free to spend \
			your points by bidding on items and purchasing raffle tickets. Please see the opening post for details on how to \
			go about that.\n\nHappy bidding!\n\n{1}".format(datetime.strftime(datetime.now(),'%Y-%m-%d %H:%M:%S'), notifybox)
			
		elif newphase == "winners":
			self.salem.setSalemConfig("NEORAFFLE_PHASE", "off")
			
			posttopic = "Winners Announced!"
			
			winners = self.raffle.pickWinners()
			
			output = "The NeoRaffle has been closed at [date]{0}[/date] and we are ready to announce the winners!\n\n \
			They are as follows: \n[ul]\n".format(datetime.strftime(datetime.now(),'%Y-%m-%d %H:%M:%S'))
			
			for win in winners:
				output += "[size=4][b]Lot {0} ({1}): [http://raffle.pwnsu.com/items/{2}/ {3}][/b][/size]\n".format(win['lot'], "Raffle" if win['type']==1 else "Auction", win['lot'], win['title'])
				output += "[i]{}x {} by {}[/i]\n\n".format(win['quantity'], "Raffled" if win['type']==1 else "Auctioned", self.neo.getForumNotifyStringForUsername(win['from']))
				
				if len(win['winners']) == 0:
					output += "No winners for this item. No-one {0} for it. :(\n\n".format("bought tickets" if win['type']==1 else "bidded")
				else:
					output += "[color=red][b]{0}[/b][/color]:\n[ul]".format("Winner" if len(win['winners']) < 2 else "Winners")
					
					for winner in win['winners']:
						output += "{0}\n".format(self.neo.getForumNotifyStringForUsername(winner))
					output += "[/ul]\n"
				
			output += "[/ul]\nThanks to everyone for participating! This is your raffle kitty signing out...\n\n{0}".format(notifybox)
		
		else:
			self.salem.send_message(channel, "** [06NeoRaffle] Invalid phase option specified. Must be: off, userreg, itemreg, bidding, winners")
			return
		
		self.neo.postToForums(thread, "NeoRaffle Phase Change: {0}".format(posttopic), output)
		self.salem.send_message(channel, "** [06NeoRaffle] Phase set to {0}.".format(newphase))	
		
	def __deleteItem(self, channel, ircmsg):
		try:
			res = self.raffle.deleteItem(ircmsg[2])
			
			# Refund bonus points from user if applicable:
			if res['owneditems']+1 <= raffleplugin.MAXBONUS:
				self.raffle.setUserAvailableCurrency(res['userid'], delta=raffleplugin.BONUSPTS - raffleplugin.BONUSPTS*2)
			
			self.salem.send_message(channel, "** [06NeoRaffle] Item {0} has been deleted!".format(ircmsg[2]))
		except DoesNotExist as e:
			self.salem.send_message(channel, "** [06NeoRaffle] {0}".format(e))
		except (KeyError, IndexError):
			self.salem.send_message(channel, "** [06NeoRaffle] You must specify a valid lot number to delete!")
			
	def __editItem(self, channel, ircmsg):
		try:
			item = ircmsg[2]
			command = " ".join(ircmsg[3:])
			
			# Build dictionary from input: title=hi whatever|description=this salemtest etc
			params = dict((k.strip(), v.strip()) for k,v in(item.split('=') for item in command.split('|')))
			
			if "title" in params.keys():
				params['htmltitle'] = self.neo.translateMarkupToHtml(params['title'])
			if "description" in params.keys():
				params['htmldescription'] = self.neo.translateMarkupToHtml(params['description'])
			
			self.raffle.editItem(item, **params)
			
			self.salem.send_message(channel, "** [06NeoRaffle] Lot {0} updated.".format(item))
		except DoesNotExist:
			self.salem.send_message(channel, "** [06NeoRaffle] The item you specified wasn't found in the DB!")
		except IndexError:
			self.salem.send_message(channel, "** [06NeoRaffle] You must specify an item and edit parameters, i.e.: @neoraffle edit 4 title=Hi|description=whatever lol")
		except ValueError:
			self.salem.send_message(channel, "** [06NeoRaffle] Invalid parameters specified! If you are trying to re-assign an item's owner, ensure who you're assigning it to is registered.")
			
	
	def __usercurrency(self, channel, ircmsg):
		try:
			user = int(ircmsg[2])
		except ValueError:
			self.salem.send_message(channel, "** [06NeoRaffle] You must provide the user's numeric ID, not username.")
			return
		try:
			newval = int(ircmsg[3])
			
			if newval < 0:
				newval = 0
				
		except IndexError:
			newval = None
		except ValueError:
			self.salem.send_message(channel, "** [06NeoRaffle] New value provided must be a positive numeric.".format(user))
			return
		
		try:
			currency = self.raffle.getUserAvailableCurrency(user)
			username = self.neo.getMemberIdFromUsernameOrId(user)
		except UserNotRegistered:
			self.salem.send_message(channel, "** [06NeoRaffle] User {0} was not registered in the DB.".format(user))
			return
		
		output = "** [06NeoRaffle] {0}'s currency is currently: {1}".format(username, currency)
		
		if not newval is None:
			self.raffle.setUserAvailableCurrency(user,newval)
			currency = self.raffle.getUserAvailableCurrency(user)
			output += ". You have adjusted it to: {0}.".format(currency)
			
		self.salem.send_message(channel, output)

class salemplugin(raffleplugin):
	pass

if __name__ == "__main__":
	pass