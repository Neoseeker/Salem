'''
Module: NeoRaffle
Author: Tiffany di Vita <tiffany@neoseeker.com>
Version: 1.1 (2014-07-31)
License: Released under WTFPL <http://www.wtfpl.net/txt/copying/>

===========
Info
===========
The NeoRaffle module was designed to facilitate the running of raffles and auctions on neoseeker.com. This module
acts as a complete database structure with fundamental raffle/auction operations included.  While this will be
used officially in conjunction with the Salem bot which includes a Neoseeker framework for interpreting and making forum
posts and receiving notifications it can be used independently with whatever data collection and presentation
methods you like.

===========
Importing
===========
from neoraffle import neoraffle, UserAlreadyRegistered, MultipleValidationErrors, DoesNotExist, \\
UserNotRegistered, InvalidAuctionType, UserCannotAffordItem, BidDoesNotExceedCurrentTopBid, UserAttemptToPurchaseOwnItem, UserAccountIsInactive

===========
Examples
===========
Simply instantiate a raffle object and go from there:

raffle = neoraffle()

Initilization of the DB will occur on instantiation. Ensure you specify correct DB settings in the settings list below.
The raffle module is capable of supporting whichever DBs SQLAlchemy is capable of using. The official raffles use a 
MariaDB <https://mariadb.org/> backend and that is the recommended choice. See help(neoraffle) for further details
of the available methods.
'''
import logging, random

from sqlalchemy import create_engine, ForeignKey
from sqlalchemy import Column, Date, Integer, String, Table, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, backref, sessionmaker
from sqlalchemy.sql.expression import func
from sqlalchemy.orm.exc import NoResultFound#
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import IntegrityError

try:
    from salemconfig import settings
except ImportError:
    settings = {
              'DBTYPE': 'sqlite',
              'CONNECTIONSTRING': 'raffle.db',
    }

# Module-level instance of logger:
log = logging.getLogger(__name__)

# Some ORM stuff for the DB - let's do some alchemy:
sqlengine = create_engine("{0}://{1}".format(settings['DBTYPE'], settings['CONNECTIONSTRING']), poolclass=NullPool)
Session = sessionmaker(bind=sqlengine)
Base = declarative_base()
Base.metadata.bind = sqlengine

#=================================================
# ORM DB classses for SqlAlchemy.
#=================================================
class Bids(Base):
    '''This table will store all bids placed to retain bid history on an item.'''
    
    __tablename__ = "bids"
    
    bid = Column('bidid', Integer, primary_key=True)
    bidderid = Column('bidderid', Integer, ForeignKey('users.uid'))
    itemid = Column('itemid', Integer, ForeignKey('auctionitems.iid'))
    
    biddate = Column('biddate', String(255), nullable=False, default=func.now())
    amount = Column('amount', Integer, nullable=False)
    
    bidder = relationship("Users", backref="bidders")
    item = relationship("AuctionItems", backref="items")
    
class TicketPurchases(Base):
    '''Table for raffle ticket assignments on purchase.'''
    
    __tablename__ = "ticketpurchases"
    
    tid = Column('tid', Integer, primary_key=True)
    ticketbuyer = Column('ticketbuyerid', Integer, ForeignKey('users.uid'))
    itemid = Column('itemid', Integer, ForeignKey('auctionitems.iid', ondelete='CASCADE'))
    
    user = relationship("Users", backref="tickets")
    item = relationship("AuctionItems", backref="tickets")

class Users(Base):
    '''Storage table for registered users with the raffle system and their currency.'''
    
    __tablename__ = "users"
    
    uid = Column(Integer, primary_key=True, nullable=False)
    username = Column(String(255), nullable=False)
    regdate = Column(String(255), nullable=False, default=func.now())
    currency = Column(Integer, nullable=False, default=0)
    heldcurrency = Column(Integer, nullable=False, default=0)
    isactive = Column(Boolean, nullable=False, default=True)

class AuctionItems(Base):
    '''Raffle items table.'''
    
    __tablename__ ="auctionitems"
    
    iid = Column(Integer, primary_key=True, nullable=False)
    title = Column(String(255), nullable=False)
    htmltitle = Column(String(255), nullable=True)
    description = Column(String(10000), nullable=False)
    htmldescription = Column(String(15000), nullable=True)
    quantity = Column(Integer, nullable=False)
    price = Column(Integer, nullable=True)
    auctiontype = Column(Integer, nullable=False)
    offeredby = Column(Integer, ForeignKey('users.uid'), nullable=False)
    
    bids = relationship("Bids", order_by="desc(Bids.amount)")
    ticketbuys = relationship("TicketPurchases")
    offered = relationship("Users", backref="owneditems")
    
    rafflewinner = relationship("Users", secondary="rafflewinners", passive_deletes=True)
    winningticket = relationship("TicketPurchases", secondary="rafflewinners", passive_deletes=True)
    
    userauctionitems = relationship("Users", backref="auctionitems")
    
class AuctionTypes(Base):
    '''Auction types go here. 1 = Raffle, 2 = Auction.'''
    
    __tablename__ = "auctiontypes"
    
    tid = Column(Integer, primary_key=True, nullable=False)
    typename = Column(String(255), nullable=False)
    
# Association table for drawing winners.
class RaffleWinners(Base):
    __tablename__ = "rafflewinners"
    
    rwid = Column(Integer, primary_key=True)
    winnerid = Column('winnerid', Integer, ForeignKey('users.uid'))
    lotid = Column('lotid', Integer, ForeignKey('auctionitems.iid', ondelete='CASCADE'))
    ticketid = Column('ticketid', Integer, ForeignKey('ticketpurchases.tid', ondelete='CASCADE'))
    
    winuser = relationship("Users")
    winitem = relationship("AuctionItems")
    winticket = relationship("TicketPurchases")


#=================================================
# Custom NeoRaffle exceptions.
#=================================================
class UserAlreadyRegistered(Exception): pass
class UserNotRegistered(Exception): pass
class MultipleValidationErrors(Exception): pass
class DoesNotExist(Exception): pass
class UserCannotAffordItem(Exception): pass
class BidDoesNotExceedCurrentTopBid(Exception): pass
class InvalidAuctionType(Exception): pass
class UserAttemptToPurchaseOwnItem(Exception): pass
class UserAccountIsInactive(Exception): pass


#=================================================
# NeoRaffle main class.
#=================================================
class neoraffle:
    '''Neoseeker raffle system.
    
    Module to handle the operation of the annual Neoseeker Raffle/Auction system and DB interactions.
    
    Attributes:
        session - DB transaction session for raffle instance.'''
    
    def __init__(self, initilize=True):
        '''Raffle class constructor.
        
        Args:
            [optional] initilize (bool) - Set to False to not run the DB initilization procedure on instantiation.'''
        
        if initilize:
            self.__initilizeRaffleDatabase()
    
    #=================================================
    # Public raffle methods.
    #=================================================
    def handleNeoraffleRegistration(self, userid, username, neopts, ggpts, postcount, wikiedits, neoptscap=2000, ggptscap=2000, postscap=20000, wikiptscap=2000, isactive=True):
        '''Register a Neo user with the raffle system.
        
        This will add a user (by MemberID) to the DB and take a snapshot of their available currency 
        at the time of registration.
        
        Args:
            userid (str) - Neoseeker MemberID of user registering.
            username (str) - Neoseeker username of member registering.
            neopts (int) - Neo points earned by user.
            ggpts (int) - Points for GameGrep submissions for registering user.
            postcount (int) - Total post count for user.
            wikiedits - Number of wiki edits made by this user to be converted into points.
            [optional] neoptscap (int) - Cap for amount of neopts that contribute to user's total. (default: 2,000)
            [optional] ggptscap (int) - Cap for amount of GameGrep points that contribute to a user's total. (default: 2,000)
            [optional] postscap (int) - Cap for amount of posts that contriute to a user's total. (default: 20,000)
            [optional] wikiptscap - Cap for amount of wiki edits that contribute to a user's points total. (default: 2,000)
        
        Returns:
            On successful registration: Hash table outlining total with currency breakdown. Keys: totalpts, neopts, ggpts, postpts, wikipts
            
        Exceptions:
            UserAlreadyRegistered - Raises this exception if the user is already registered.'''

        availableCurrency = {'neopts':0, 'ggpts':0, 'postpts':0, 'wikipts':0, 'totalpts':0}
        
        log.debug("Received NeoRaffle registration request from {0}".format(username))

        # Need to ensure they're not already registered:
        try:
            res = self.isUserRegistered(userid)
        except:
            log.exception("Unexpected error occurred when checking user registration status.")
            raise
        
        if res:
            log.debug("User {0} is attempting to register but a record for them already exists in the DB!".format(username))
            raise UserAlreadyRegistered("User {0} attempted to register but already has an entry in the DB!".format(userid))
        
                
        # Begin handling entry into the DB:
        availableCurrency['neopts'] = self.__pointsCalc(neopts, neoptscap)
        availableCurrency['ggpts']  = self.__pointsCalc(ggpts, ggptscap)
        availableCurrency['postpts'] = self.__pointsCalc(postcount, postscap)
        availableCurrency['wikipts'] = self.__pointsCalc(wikiedits, wikiptscap)
        
        # Add ALL the values. >:-(
        availableCurrency['totalpts'] = sum(availableCurrency.itervalues())
    
        # Add record to DB:
        try:
            self.__session = Session()
            self.__session.add(Users(uid=userid, username=username, currency=availableCurrency['totalpts'], isactive=isactive))
            self.__session.commit()
        except:
            log.exception("Fatal error attempting to insert user info into Neo Raffle registering DB.  UserID: {0}".format(userid))
            raise
        finally:
            self.__session.close()
            
        log.debug("Returning: {0}".format(availableCurrency))
        return availableCurrency
    
    
    def addItemToDatabase(self, userid, itemtitle, itemdescription, itemprice, itemquantity, itemtype, htmltitle=None, htmldescription=None):
        '''Add auction/raffle items to the DB.
        
        Args:
            userid (str) - Neoseeker MemberID of user adding the item.
            itemtitle (str) - Title of the item to be added.
            itemdescription (str) - Description of the item.
            itemprice (int) - Item cost.
            itemquantity (int) - Item quantity.
            itemtype (int) - Numeric ID matching type in AuctionTypes table (1: Raffle or 2: Auction).
            [optional] htmltitle (str) - Storage area for HTML version of item title for web display.
            [optional] htmldescription (str) - Storage area for HTML version of description for web display.
        
        Returns:
            (int) ID of the newly added item on success.
            
        Exceptions:
            MultipleValidationErrors - Raises this exception with list of errors on input validation failures.'''
        
        try:
            self.__session = Session()
            
            errItems = []
            logErr = "Validation error when adding item to DB by user {0}".format(userid)
            
            # Get user item:
            try:    
                user = self.__getUserFromMemberId(userid)
            except UserNotRegistered:
                raise("You must be registered to offer items!")
            
            # Remove any commas from the price/quantity:
            try:
                itemprice = itemprice.replace(',','')
                itemquantity = itemquantity.replace(',','')
            except (AttributeError, TypeError):
                pass # They're null/ints, which is fine and expected anyway.
            
            if len(itemtitle) == 0:
                log.error("{0} - Invalid title!".format(logErr))
                errItems.append("The title of the submitted form was invalid.")
            if len(itemdescription) == 0:
                log.error("{0} - Invalid description!".format(logErr))
                errItems.append("The description of the submitted form was invalid.")
                
            if not itemtype == 2: # Don't validate price field for auctions.
                try:
                    itemprice = int(itemprice)
                    if itemprice <= 0 or itemprice > 10000:
                        log.error("{0} - price out of bounds.".format(logErr))
                        errItems.append("The price specified was out of bounds! Must be between 1 and 10,000")
                except ValueError: # The int cast failed - it's not a number.
                    log.error("{0} - price was not valid integer.".format(logErr))
                    errItems.append("Price was not a valid number.")
            else: # Auctions have no pre-defined price, set it to null for the DB call.
                itemprice = None
                
            try:
                itemquantity = int(itemquantity)
                if itemquantity <= 0 or itemquantity > 10:
                    log.error("{0} - quantity out of bounds.".format(logErr))
                    errItems.append("The quantity specified was out of bounds! Must be between 1 and 10!")
            except ValueError:
                log.error("{0} - Quantity was not valid integer.".format(logErr))
                errItems.append("Quantity was not a valid number.")
                
            if errItems:
                log.debug("Form from user {0} was rejected due to validation errors: {1}".format(userid, ", ".join(errItems)))
                raise MultipleValidationErrors(*errItems)
    
            # Continue with adding the items if all is ok:
            try:
                item = AuctionItems(title=itemtitle, description=itemdescription, quantity=itemquantity, price=itemprice, auctiontype=itemtype, offered=user)
                
                if htmltitle:
                    item.htmltitle = htmltitle
                if htmldescription:
                    item.htmldescription = htmldescription
        
                self.__session.add(item)
                self.__session.commit()
                
                itemid = item.iid
            except:
                log.exception("Error when writing item to DB!")
                raise
            
            return itemid
        finally:
            self.__session.close()
    
    
    def makePurchase(self, purchasetype, userid, itemid, **kwargs):
        '''Interface method to handle purchase requests.
        
        Args:
            purchasetype (str) - Type of purchase to process. Accepted: raffle, auction
            userid (str) - Neoseeker member ID of user making the purchase.
            itemid (str) - Lot number of the item being purchased.
            **kwargs - Purchase type specific values. quantity should be present for raffles. bid should be present for auctions.
            
        Returns:
            For raffle items, see returns of method: __buyRaffleTickets
            For auction items, see returns of method: __bidOnItem
            
        Exceptions:
            UserNotRegistered - User making the purchase isn't registered with the system.
            DoesNotExist - The lot number was not found in the DB.
            ValueError - The parameters passed to the method were invalid.
            UserAttemptToPurchaseOwnItem - Raised if the user making the bid owns the item.
            UserCannotAffordItem - User is attempting to make a purchase but doesn't have required currency.
            InvalidAuctionType - Raised if item auction type doesn't match the operation being performed, i.e. trying to bid on a raffle item.
            BidDoesNotExceedCurrentTopBid - Bid placed was too low.
            UserAccountIsInactive - User is registered, but their account is set to inactive.
        '''
        try:    
            types = {"raffle": self.__buyRaffleTickets, "auction": self.__bidOnItem}
        
            self.__session = Session()
            
            try:
                user = self.__getUserFromMemberId(userid)
            except NoResultFound:
                log.error("User {0} attempting attepting to make a purchase on lot {1} but was not registered!".format(userid, itemid))
                raise UserNotRegistered("The userid specified ({0}) is not registered with the NeoRaffle system".format(userid))    
        
            try:
                item = self.__getItemFromLotNumber(itemid)
            except DoesNotExist:
                log.error("User {0} attempting to make a purchase on lot {1} but the lot number wasn't found in the DB!".format(userid, itemid))
                raise DoesNotExist("Lot {0} was not found in the DB!".format(itemid))
            
            if user.isactive is False:
                raise UserAccountIsInactive("Inactive users cannot make purchases!")
            
            if user.uid == item.offeredby: # User is attempting to bid on own item!
                raise UserAttemptToPurchaseOwnItem("You cannot buy tickets or bid for your own item!")
            
            try:
                auctiontype = self.__session.query(AuctionTypes).filter(AuctionTypes.typename == purchasetype.capitalize()).one()  
            except NoResultFound:
                raise ValueError("Invalid purchase type passed to method: {0}!".format(purchasetype))
            
            if not auctiontype.tid == item.auctiontype: # User is attempting to make a bid on a raffle item or trying to buy an auction item.
                raise InvalidAuctionType("Method call invalid - invoked {0} call but the item does not match that lot type.".format(purchasetype))
            
            try:
                return types[purchasetype](user, item, **kwargs)
            except KeyError:
                raise ValueError("Supported purchase types are: {0}".format(", ".join(types.keys())))
            except TypeError as e:
                raise ValueError("Parameters were not expected by underlying method. Args: {0}. Err: {1}".format(", ".join(kwargs), e))
            except UserCannotAffordItem:
                raise
            except BidDoesNotExceedCurrentTopBid:
                raise
            except:
                log.exception("An unknown error occurred when running the purchase routine.")
                raise
        finally:
            self.__session.close()
            
            
    def pickWinners(self):
        '''Method which will populate the rafflewinners table with n*quatity winners for each raffle item.
        
        The method used to select winners generates n*quantity unique winners for each raffle item in the DB.
        If a user is selected as a winner, their remaining tickets will be removed from subsequent random
        rolls.  For auction items, the winner is simply the highest current bidder at the time.
        
        Args:
            None.
        
        Returns:
            List of dics containing winner informatinon for each item in the DB:
                [
                 { 
                     lot -> ID of the lot.
                     from -> Username of person who put up the lot.
                     title -> Title of the item.
                     type -> lot type
                     winners -> list of usernames drawn as winners for the lot.
                 }
                ]
        '''
        try:
            session = Session()
            
            # Get all items:
            items = session.query(AuctionItems).all()
            tickets = []
            rtn = []
            
            # Clear existing winners:
            session.execute("TRUNCATE TABLE rafflewinners;")     
            
            for item in items:
                winners = []
                
                if item.auctiontype==1 and item.ticketbuys:                   
                    tickets = item.ticketbuys
                    random.shuffle(tickets) # Shuffle list of tickets first to improve randomness.
                    
                    for _ in xrange(item.quantity):
                        try:
                            winner = random.sample(tickets,1) # Get one winner.
                        except ValueError:
                            break # There's less ticket purchases than there are quantity of items.
                        log.debug("Winner!! (Quant: {0}, ItemID: {1}) = {2} {3}".format(item.quantity, item.iid, winner[0].tid, winner[0].user.username))
                        
                        # Remove user's remaining tickets and shuffle again for next draw to create unique winners:
                        tickets = [ticket for ticket in tickets if not ticket.user == winner[0].user]
                        random.shuffle(tickets) # Shuffle the new list again.
                        
                        # Add winner to DB:
                        win = RaffleWinners(winuser=winner[0].user, winitem=item, winticket=winner[0])
                        session.add(win)
                        session.commit()
                        
                        # Append winner for return:
                        winners.append(winner[0].user.username)
                    
                elif item.auctiontype == 2 and item.bids:
                    # Just append the current top bidder as the winner for auctions:
                    winners.append(item.bids[0].bidder.username)
                else:
                    log.debug("No tickets purchased for item: {0} when running pick winners routine.".format(item.iid))
                
                # Add item for return:
                rtn.append({'lot':item.iid,'from':item.offered.username,'title':item.title,'type':item.auctiontype,'winners':winners})
                    
            return rtn
        finally:
            session.close()
            
    def isUserRegistered(self, userid):
        '''Determines if a user has registered with the NeoRaffle system.
        
        Args:
            userid (int) - Neoseeker MemberID of user to check.
        
        Returns:
            True if user is registered.  False otherwise.'''
        
        res = False
        
        try:
            session = Session()
            res = True if session.query(Users).filter(Users.uid==userid).count() else False
        except:
            log.exception("Fatal error performing a lookup on the users table.")
            raise
        finally:
            session.close()        

        return res
    
    def getNumOwnedItems(self, userid):
        '''Return the number of items a user has put up for raffle/auction.
        
        Args:
            userid - Neoseeker member ID.
        '''
        try:
            self.__session = Session()
            user = self.__getUserFromMemberId(userid)
            
            return int(len(user.owneditems))
        except UserNotRegistered:
            raise
        finally:
            self.__session.close()
            
        
    def getUserAvailableCurrency(self, userid):
        '''Return a user's available currency from Neo user ID.
        
        Args:
            userid (str) - Neoseeker member ID.
            
        Returns:
            (int) Value of user's remaining currency.
            
        Exceptions:
            UserNotRegistered - Raised if user isn't registered with the system.
        '''
        try:
            self.__session = Session()
            user = self.__getUserFromMemberId(userid)
            availcur = (user.currency - user.heldcurrency)
            
            return availcur
        except UserNotRegistered:
            raise
        finally:
            self.__session.close()
        
    def setUserAvailableCurrency(self, userid, newcurrency=None, delta=None):
        '''Method to manually adjust a user's currency for providing bonuses for contest winnners, etc.
        
        Args:
            userid - Neoseeker member ID.
            [optional] newcurrency - Value to set currency to. Required if no delta specified.
            [optional] delta - If supplied, will add this number to the current currency instead. Required if no newcurrency specified.
        
        Exceptions
            UserNotRegistered - Raise if user provided isn't registered.
        '''
        try:
            self.__session = Session()
            user = self.__getUserFromMemberId(userid)
            
            if delta:
                user.currency += delta
            else:
                user.currency = newcurrency
            
            self.__session.commit()
        except UserNotRegistered:
            raise
        finally:
            self.__session.close()
    
    def fetchRegisteredUsers(self):
        '''Method to return a list of all NeoRaffle registered users.'''
        try:
            session = Session()
            users = session.query(Users).all()
            rtn = [user.username for user in users]
            
            return rtn
        finally:
            session.close()
            
    def deleteItem(self, itemid, userid=None):
        ''' Method to delete an item by ID from the DB.
        
        Args:
            itemid - Lot number of the item to delete.
            [optional] userid - If provided, will only let the calling user ID delete their own items.
            
        Exceptions:
            DoesNotExist - Will be raised if item is not found in the DB.
            UserNotRegistered - Raised if passed user ID isn't registered with the system.
            ValueError - Raised if user is attempting to delete an item which they do not own.
            
        Return:
            Dict:
                {"userid" => ID of user who item belonged to.
                 "owneditems" => Number of items user owns after deletion.'''
        
        try:
            self.__session = Session()
            item = self.__getItemFromLotNumber(itemid)
            
            uid = item.offered.uid
            itemsowned = len(item.offered.owneditems)-1
            
            if userid:
                user = self.__getUserFromMemberId(userid)
                
                if not user.uid == item.offeredby:
                    raise ValueError("You cannot delete items which do not belong to you!")
            
            self.__session.delete(item)
            self.__session.commit()
            
            return {"userid":uid, "owneditems":itemsowned}
        finally:
            self.__session.close()
            
    def editItem(self, itemid, **kwargs):
        '''Method to edit an item by ID from the DB.
        
        Args:
            itemid - Lot number to edit.
            **kwargs - Params that match item ORM fields.
        
        Exceptions:
            DoesNotExist - Raised if item specified was not found.
            ValueError - Raised if edit attempt tries to assign item to a user ID not in the users table.
        '''
        try:
            self.__session= Session()
            item = self.__getItemFromLotNumber(itemid)
            
            for k,v in kwargs.items():
                setattr(item,k,v)
                                
            self.__session.commit()
                
        except DoesNotExist:
            raise
        except IntegrityError:
            raise ValueError("Cannot change owner of this item as the user specified is not registered!")
        finally:
            self.__session.close()
        
    
    #=================================================
    # Raffle private methods. Not to be used directly.
    #=================================================
    def __pointsCalc(self, points, cap):
        '''Method to return number of points as per the cap specified and not less than 0.
        
        Args:
            points - Number of points.
            cap - Cap to implement.
        '''
        try:
            points = int(points)
            cap = int(cap)
        except TypeError:
            points = 0 # None was passed so points are 0.
        
        if points < 0:
            points = 0  
        elif points > cap:
            points = cap
            
        return points
    
    
    def __initilizeRaffleDatabase(self):
        '''Initilize raffle database for use.
        
        This is called from the constructor to ensure a DB file with sane copy of the required schema exists
        and is ready to be used with the raffle system each time it is instantiated.
        
        Args:
            N/A
        
        Returns:
            True on success.  Exception when initilization failed.'''
        try:                            
            # Create necessary schema:
            Base.metadata.create_all(sqlengine)
                        
            # Add the raffle/auction types to the fresh types table:
            self.__session = Session()
            self.__session.query(AuctionTypes).delete()            
            self.__session.add_all([AuctionTypes(tid=1,typename="Raffle"),AuctionTypes(tid=2,typename="Auction")])
            self.__session.commit()
        except:
            log.exception("There was an error initilizing the Neo Raffle DB!")
            raise
        finally:
            self.__session.close()
            
        return True
    
     
    def __buyRaffleTickets(self, user, item, quantity):
        '''Process a user's request to buy raffle tickets.  Requires active session attribute.
        
        Args:
            user (obj) - ORM User object of purchasing user.
            item (obj) - ORM Item object of item.
            quantity (int) - Number of tickets to buy.
        
        Returns:
            (dict) Cost info and raffle ticket numbers purchased:
                iteminfo:
                    =>lotnum - Lot ID
                    =>title - Lot title.
                costinfo:
                    =>ticketprice - cost of a single ticket for the lot.
                    =>totalcost - total cost of the purchase.
                tickets - list of ticket numbers.
            
            List of raffle ticket numbers assigned to the user for the raffle lot depending on quantity requested.
        
        Exceptions:
            ValueError - Quantity passed was invalid.  Must be number above 0.
            UserCannotAffordItem - Cost exceeds user's available currency.
        '''
        ticketnums = []
        
        try:
            if "," in quantity:
                quantity = quantity.replace(",","")
                
            quantity = int(quantity)
            
            if quantity <= 0:
                raise ValueError
        except:
            raise ValueError("{0} is not a valid quantity!".format(quantity))
        
        purchasecost = item.price * quantity
              
        # Make the purchase - update user held currency:
        try:
            self.__updateHeldCurrency(user, purchasecost)
        except UserCannotAffordItem:
            raise
        
        # Return list of ticket numbers:
           
        for _ in range(0, quantity):
            tickets = TicketPurchases(user=user, item=item)
            self.__session.add(tickets)
            self.__session.flush()
            
            ticketnums.append(tickets.tid)
        
        self.__session.commit()
            
        return {'iteminfo':{'lotnum':item.iid, 'title':item.title},'costinfo':{'ticketprice':item.price,'totalcost':purchasecost},'tickets':ticketnums}

    
    def __updateHeldCurrency(self, user, cost):
        '''Update a user's held currency.  Can also handle refunds by passing negative values.  Requires active session attribute.
        
        Args:
            user (obj) - User ORM object for purchaser.
            cost (int) - Amount of currency to hold for given user.
            
        Exceptions:
            UserCannotAffordItem - Raised when passed cost exceeds user's available currency.
        '''
        availcur = (user.currency - user.heldcurrency)
        if cost > availcur:
            raise UserCannotAffordItem("Cost of the purchase is {0}, but user only has {1} points remaining!".format(cost, availcur))
        
        user.heldcurrency += cost
        self.__session.commit()
                
    
    def __bidOnItem(self, user, item, bid):
        '''Process a user's bid on a raffle/auction item.  Requires active session attribute.
        
        Args:
            user (obj) - User ORM object for purchaser.
            item (obj) - Item ORM object for item.
            bid (int) - Currency to bid for item.
        
        Returns:
            Dictonary on success detailing former and current top bidders:
                iteminfo:
                    =>lotnum
                    =>title
            
                prevtopbidder:
                    =>userid
                    =>amount
                newtopbidder:
                    =>usesrid
                    =>amount

        Exceptions:
            ValueError - Raised if the bid quantity is invalid.
            BidDoesNotExceedCurrentTopBid - If user's bid doesn't exceed current top bid, this will be raised.
            UserCannotAffordItem - User is attempting to bid on an item but doesn't have the available funds.
        '''        
        try:
            if "," in bid:
                bid = bid.replace(",","")
                
            bid = int(bid)
        except:
            raise ValueError("{0} isn't a valid bid quantity!".format(bid))
        
        try:
            curtopbid = item.bids[0].amount
            curtopbidder = item.bids[0].bidder
            curtopbidderid = curtopbidder.uid
        except IndexError:
            curtopbid, curtopbidder, curtopbidderid = None, None, None # There were no bids yet.
            
                
        if curtopbid >= bid:
            raise BidDoesNotExceedCurrentTopBid("The bid of {0} did not exceed the current top bid for lot {1}, which is: {2}".format(bid, item.iid, curtopbid))

        # Firstly, refund the previous top bidder. This will allow a user to outbid themselves as per request:
        if curtopbid:
            try:
                self.__updateHeldCurrency(curtopbidder, curtopbid-(curtopbid*2))
            except:
                log.exception("Critical error refunding bid!")
                raise
        
        # Remove the new bid from the bidder's currency:    
        try:
            self.__updateHeldCurrency(user, bid)
        except: # If any errors occur at all during this, we need to try to reverse the refund:
            self.__updateHeldCurrency(curtopbidder, curtopbid)
            raise
        
        # Commit the bid to the DB if all went well and return:
        procbid = Bids(bidder=user, item=item, amount=bid)
        
        self.__session.add(procbid)
        self.__session.commit()
            
        return {'iteminfo':{'lotnum':item.iid, 'title':item.title},'prevtopbidder':{'userid':curtopbidderid,'amount':curtopbid},'newtopbidder':{'userid':user.uid,'amount':bid}}
        

    def __getUserFromMemberId(self, neomemberid):
        '''Return a DB user object from a Neo member ID.  Requires active session attribute.
        
        Args:
            neomemberid - MemberID of Neo user.
        
        Returns:
            User ORM object.
        
        Exceptions:
            UserNotRegistered - Returned if user is not registered with the raffle system.'''
        
        try:
            user = self.__session.query(Users).filter(Users.uid == neomemberid).one()
        except NoResultFound:
            raise UserNotRegistered("Attempted to get DB object for user {0} but wasn't found in DB!".format(neomemberid))
        
        return user
    
    def __getItemFromLotNumber(self, lotnumber):
        '''Return an Item DB object reference from a lot number.  Requires active session attribute.
        
        Args:
            lotnumber (int) - Lot number.
        
        Returns:
            Item ORM object.
        
        Exceptions:
            DoesNotExist - Returned if item is not found within the DB.'''
        
        try:
            item = self.__session.query(AuctionItems).filter(AuctionItems.iid == lotnumber).one()
        except NoResultFound:
            raise DoesNotExist("Call to fetch item {0} from the DB, but it doesn't exist!".format(lotnumber))
        
        return item

    
if __name__ == "__main__":
    logging.basicConfig(level="DEBUG")