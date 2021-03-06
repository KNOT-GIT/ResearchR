#!/bin/env python
#-*- coding: utf-8 -*-
import psycopg2
import ConfigParser
import sys, getopt
import hashlib
import unicodedata
from StringIO import StringIO
from time import strptime
import logging
import random
import time

from rrslib.db.model import *
from rrslib.db.dbal import PostgreSQLDatabase, FluentSQLQuery
from rrslib.db.xmlimport import RRSXMLImporter, LOOKUP_FAST, LOOKUP_PRECISE
from rrslib.db.dbal import PostgreSQLDatabase, RRSDatabase, DatabaseError, RRSDB_MISSING, EXEC_LOG
from rrslib.extractors.normalize import Normalize
from rrslib.xml.xmlconverter import Model2XMLConverter

from researchr import *

class RPublication:
	def __init__(self):
		self.abstract = None
		self.address = None
		self.authors = []
		self.person = []
		self.booktitle = None
		self.conference = None
		self.conferenceYear = None
		self.doi = None
		self.editors = []
		self.firstpage = None
		self.key = None
		self.issuenumber = None
		self.journal = None
		self.key = None
		self.lastpage = None
		self.month = None
		self.note = None
		self.number = None
		self.organization = None
		self.publisher = None
		self.series = None
		self.title = None
		self.publication_type = None
		self.url = None
		self.volume = None
		self.volumenumber = None
		self.year = None

class ResearchrPublicationFeeder:
	def __init__(self, config, importer_kwargs):
		#data ziskana z api
		self.rPublication = None
		
		#objekt typu RRSPublication, ktery po naplneni budeme importovat do db
		self.publication = None

		#nastaveni pro importer
		self.importer_kwargs = importer_kwargs

		#sleeper range
		self.LimitMin = 0.1
		self.LimitMax = 0.2

		#objekt pro vytvareni sql dotazu
		self.q = FluentSQLQuery()

		#researchr API
		self.researchrClass = ResearchrClass()

		#nejvyssi vrstva, pro nacteni objektu podle id
		self.rrsdb = RRSDatabase()

		#normalizator
		self.norm = Normalize()
		
		#importer
		self.importer = RRSXMLImporter(self.importer_kwargs)

	def __FillType(self):
		"""
		Transform rPublication.type to publication.type
		"""
		_id = self.__GetId("publication_type", "type=", self.rPublication.publication_type)
		if (_id != None):
			self.publication["type"] = self.rrsdb.load("publication_type", _id)
	

	def __FillSeries(self):
		"""
		Add rPublication.series to publication_series table
		"""
		if (self.rPublication.series != None and self.rPublication.series != ""):
			_id = None
			while (_id == None):
				_id = self.__GetId("publication_series", "title=", self.rPublication.series)
				if (_id == None):
					series = RRSPublication_series(title=self.rPublication.series)
					#importer = RRSXMLImporter(self.importer_kwargs)
					self.importer.import_model(series)
					continue
			self.publication["series"] = self.rrsdb.load("publication_series", _id)
			

	def __GetId(self, _from, where, _is):
		"""
		Try to find ID in table and return it
		
		@type  _from: string
		@param _from: Name of table.
		@type  where: string
		@param where: Name of column.
		@type  _is: string
		@param _is: What it is equal.
		@rtype:   int
		@return:  Id of selected entry.
		"""
		self.q.select("id").from_table(_from)
		self.q.where(where, _is)
		self.q()
		data = self.q.fetch_one()
		#print(self.q.sql())
		self.q.cleanup()
		if data != None:
			return data[0]
		return None
	
	def __FillPublisher(self):
		"""
		Add rPublication.publisher to organization table
		"""
	 	if (self.rPublication.publisher != None and self.rPublication.publisher != ""):
			_id = None
			normalized_title = self.norm.organization(self.rPublication.publisher)
			while (_id == None):
				_id = self.__GetId("organization", "title_normalized=", normalized_title)
				if (_id == None):
					organization = RRSOrganization(title=self.rPublication.publisher, 
						title_normalized=normalized_title)
					#importer = RRSXMLImporter(self.importer_kwargs)
					self.importer.import_model(organization)
					continue
				self.publication["publisher"] = self.rrsdb.load("organization", _id)

	def __FillAuthors(self, authorData, isEditor):
		"""
       		FillAuthor Add (if there are not) person to db and
       		contain them with actual publication. Foreach
		rPublication.authors, take only person's url and fullname.
		
		@type  authorData: list
		@param authorData: List of authors data (person, alias)
		@type  isEditor: bool
		@param isEditor: True if authors are editors of this publication.
		"""
		if (len(authorData) != 0):
			rank = 0
			for author in authorData:
				if 'author' in author:
					rFullname = author["person"]["fullname"]
					rUrl = author["person"]["url"]
				else:
					rFullname = author["alias"]["name"]
					rUrl = author["alias"]["url"]
				personUrl = RRSRelationshipPersonUrl()
				rank += 1
				self.__FillUrl(personUrl, rUrl)
				self.__FillPerson(personUrl, rFullname, rank, isEditor)

	def __FillUrl(self, personUrl, rUrl):
		"""
		This function add url to db bind url to person 

		@type  personUrl: RRSRelationshipPersonUrl
		@param personUrl: Relationship object to add url into it.
		@type  rUrl: string
		@param isEditor: rPublication.(person/alias) url, url of author/editor.
		"""
		_id = None
		while (_id == None):
			_id = self.__GetId("url", "link=", rUrl)
			if (_id == None):	
				url = RRSUrl(link=rUrl)
				url["type"] = self.rrsdb.load("url_type", "1")
				#importer = RRSXMLImporter(self.importer_kwargs)
				self.importer.import_model(url)	
				continue
			url = self.rrsdb.load("url", _id)
			personUrl.set_entity(url)
			#print( personUrl)

	def __FillPerson(self, personUrl, rFullname, rank, isEditor):
		"""
		This function try fill first name, middle name, last name of person.

		@type  personUrl: RRSRelationshipPersonUrl
		@param personUrl: Relationship object to bind to person["url"].
		@type  rFullname: string
		@param rFullname: Fullname of author.
		@type  rank: int
		@param rank: Rank of author, first author get 1, second 2 and so on.
		@type  isEditor: bool
		@param isEditor: True if person is editor of this publication.
		"""
		_id = None
		while (_id == None):
			_id = self.__GetId("person", "full_name=", rFullname)
			if (_id == None):
				person = RRSPerson()
				person["full_name"] = rFullname
				person["url"] = personUrl
				self.__SetPersonNames(person, rFullname)
				person["full_name_ascii"] = unicodedata.normalize('NFKD', rFullname).encode('ascii', 'ignore')
				#importer = RRSXMLImporter(self.importer_kwargs)
				#print(person)
				self.importer.import_model(person)
				continue
			publicationPerson = RRSRelationshipPersonPublication(author_rank=rank, editor=isEditor)
			publicationPerson.set_entity(self.rrsdb.load("person", _id))
			#print(publicationPerson)
			self.publication['person'] = publicationPerson

	def __SetPersonNames(self, person, rFullname):
		"""
		This function try fill first name, middle name, last name of person.

		@type  person: RRSPerson
		@param person: Object of author of publication.
		@type  rFullname: string
		@param rFullname: Fullname of author.
		"""
		splitName = rFullname.split()
		if (len(splitName) == 3):
			person["first_name"] = splitName[0]
			person["middle_name"] = splitName[1]
			person["last_name"] = splitName[2]
		elif (len(splitName) == 2):
			person["first_name"] = splitName[0]
			person["last_name"] = splitName[1]

	def FillPublication(self, key):
		"""
		This function call all private function with prefix Fill, 
		this function load data to rPublication structure and then 
		assign data from rPublication to publication(RRSPublication).
		
		@type  key: string
		@param key: Key of the publication.
		"""
		self.__FillRPublication(key)
		self.publication = RRSPublication()
		self.__FillAuthors(self.rPublication.authors, False)
		self.__FillAuthors(self.rPublication.editors, True)
		self.__FillPublisher()
		self.__FillType()
		self.__FillSeries()
		self.publication["title"] = self.rPublication.title
		self.publication["title_normalized"] = self.norm.publication(self.rPublication.title)

		if (self.rPublication.year != None and self.rPublication.year != ""):
			self.publication["year"] = int(self.rPublication.year) # "2000" -> 2000

		if (self.rPublication.month != None and self.rPublication.month != ""):
			self.publication["month"] = int(strptime(self.rPublication.month[:3],'%b').tm_mon)

		if (self.rPublication.volume != None and self.rPublication.volume != "" and self.rPublication.volume.isdigit()):
			self.publication["volume"] = int(self.rPublication.volume)

		if (self.rPublication.number != None and self.rPublication.number != "" and self.rPublication.volume.isdigit()):
			self.publication["number"] = int(self.rPublication.number)

		if (self.rPublication.abstract != None and self.rPublication.abstract != ""):
			self.publication["abstract"] = self.rPublication.abstract

		if (self.rPublication.doi != None and "http://dx.doi.org/" in self.rPublication.doi):
			self.publication["doi"] = self.rPublication.doi.strip('http://dx.doi.org/')

		if (self.rPublication.firstpage != None and self.rPublication.lastpage != None and 
			self.rPublication.firstpage != "" and self.rPublication.lastpage != ""):
			self.publication["pages"] = str(self.rPublication.firstpage) + " - " + str(self.rPublication.lastpage)

		self.publication["language"] = self.rrsdb.load('language', 1)
		self.publication.set("researchr_key", self.rPublication.key, strict=False)
		#print(self.publication)
		#importer = RRSXMLImporter(self.importer_kwargs)
		try:
			self.importer.import_model(self.publication)
		except RRSDatabaseEntityError as e:
			print('RRSDatabaseEntityError - %s, %s' % (self.rPublication.key, str(e)))
			logging.warning('RRSDatabaseEntityError - %s, %s' % (self.rPublication.key, str(e)))
		except DatabaseError as e:
			print('DatabaseError - %s, %s' % (self.rPublication.key, str(e)))
			logging.warning('DatabaseError - %s, %s' % (self.rPublication.key, str(e)))
		except TypeError as e:
			print('TypeError - %s, %s' % (self.rPublication.key, str(e)))
			logging.warning('TypeError - %s, %s' % (self.rPublication.key, str(e)))
		except:
			print('Unexpected error - %s, %s' % (self.rPublication.key, sys.exc_info()[0]))
			logging.warning('Unexpected error - %s, %s' % (self.rPublication.key, sys.exc_info()[0]))

	def __FillRPublication(self, key):
		"""
		Fill rPublication object.

		@type  key: string
		@param key: Name od publication.	
		"""
		self.rPublication = RPublication()
		publicationData = self.researchrClass.getPublication(key)
		time.sleep(random.uniform(self.LimitMin, self.LimitMax))
		#print(publicationData)
		for key, value in publicationData.items():
			if key == 'abstract':
				self.rPublication.abstract = value
			elif key == 'address':
				self.rPublication.address = value
			elif key == 'authors':
				self.rPublication.authors = value
			elif key == 'booktitle':
	     			self.rPublication.booktitle = value
			elif key == 'conference':
	    			self.rPublication.conference = value
			elif key == 'conferenceYear':
	     	       		self.rPublication.conferenceYear = value
			elif key == 'doi':
	     	       		self.rPublication.doi = value
			elif key == 'editors':
				self.rPublication.editors = value
			elif key == 'firstpage':
	     	       		self.rPublication.firstpage = value
			elif key == 'key':
				self.rPublication.key = value
			elif key == 'issuenumber':
				self.rPublication.issuenumber = value
			elif key == 'journal':
				self.rPublication.journal = value
			elif key == 'key':
				self.rPublication.key = value
			elif key == 'lastpage':
	     	       		self.rPublication.lastpage = value
			elif key == 'month':
	     	       		self.rPublication.month = value
			elif key == 'note':
				self.rPublication.note = value
	     		elif key == 'number':
	     	       		self.rPublication.number = value
	     		elif key == 'organization':
	  	   		self.rPublication.organization = value
	  	   	elif key == 'publisher':
	     			self.rPublication.publisher = value
	     		elif key == 'series':
	     			self.rPublication.series = value
	  	   	elif key == 'title':
	  	   		self.rPublication.title = value
	  	   	elif key == 'type':
	 			self.rPublication.publication_type = value
	     		elif key == 'url':
	     			self.rPublication.url = value
	     		elif key == 'volume':
	   			self.rPublication.volume = value
	    		elif key == 'volumenumber':
				self.rPublication.volumenumber = value
	     		elif key == 'year':
		    		self.rPublication.year = value

def main(argv):
	"""
	Main function.

	
	"""
	#load config file
	config = ConfigParser.RawConfigParser()
	config.read('app.ini')

	#logging setting
	logging.basicConfig(filename='error.log',level=logging.DEBUG)

	#importer setting
	importer_kwargs = {
			'update_rule':  RRSDB_MISSING,      # jak se bude chovat updatovani radku pokud se vkladaji data do jiz existujiciho radku
			'lookup_level': LOOKUP_PRECISE,    # uroven zanoreni pri vyhledavani shodnych entit na zaklade topologie
			'logs':	 	EXEC_LOG,		# uroven logovani: informacni (status msg) a exekutivni log (update, insert)
			'logfile':      None,      # cesta a jmeno logovaciho souboru
			'module':       'rrs_import',  # jmeno modulu, ktery s daty pracuje
			'schema':       'data_researchr_test'  # databazove schema, do ktereho hodlame data nahrat
			}

	db = PostgreSQLDatabase(importer_kwargs['logfile'])
	db.connect(host=config.get("Database","host"),
		dbname=config.get("Database","db"),
		user=config.get("Database","user"),
		password=config.get("Database","pass"))
	db.set_schema(config.get("Database","schema"))

	# load names from file
	keys = loadFile(getParam(argv))

	# foreach names
	for key in keys.split('\n'):
		print(key)
		if (checkIfImport(key) == None):
			feeder = ResearchrPublicationFeeder(config, importer_kwargs)
			feeder.FillPublication(key)

def checkIfImport(key):
	"""
	Check if publication is in database.
	
	@type name: string
	@param name: Key of publication.
	"""
	q = FluentSQLQuery()
	q.select("id").from_table("publication")
	q.where("researchr_key=", key)
	q()
	data = q.fetch_one()
	return data

def loadFile(filename):
	"""
	Open file with publications names

	@type fillename: string
	@param fillename: Name of file with publications keys.
	"""
	try:
		f = open(filename, 'r')
   	except IOError:
		print "cannot open %s" % filename
		exit(2)
	data = f.read()
	return data

def getParam(argv):
	"""
	Process parameter

	@type argv: string
	@param argv: Argument of command line.
	"""
	try:
		opts, args = getopt.getopt(argv,"hi:",["ifile="])
	except getopt.GetoptError:
	 	print 'researchrFeed.py -i <inputfile>'
	 	sys.exit(2)
	for opt, arg in opts:
		if opt == '-h':
		      print 'researchrFeed.py -i <inputfile>'
		      sys.exit(2)
	 	elif opt in("-i", "--ifile"):
		      return arg

if __name__ == "__main__":
	main(sys.argv[1:])

