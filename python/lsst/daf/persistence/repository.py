#!/usr/bin/env python

#
# LSST Data Management System
# Copyright 2016 LSST Corporation.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#

import collections
import copy
import inspect
import itertools
import os
import uuid

from lsst.daf.persistence import Access, Policy, Mapper, LogicalLocation, ButlerLocation, Storage, listify


class RepositoryArgs(object):

    def __init__(self, root=None, cfgRoot=None, mapper=None, mapperArgs=None, tags=None, 
                 mode=None):        
        self.root = root
        self.cfgRoot = cfgRoot
        self.mapper = mapper
        self.mapperArgs = mapperArgs
        self.tags = set(listify(tags))
        self.mode = mode
        self.isLegacyRepository = False

    def __repr__(self):
        return "%s(root=%r, cfgRoot=%r, mapper=%r, mapperArgs=%r, tags=%s, mode=%r)" % (
            self.__class__.__name__, self.root, self.cfgRoot, self.mapper, self.mapperArgs, self.tags, 
            self.mode)

    @staticmethod
    def inputRepo(storage, tags=None):
        return RepositoryArgs(storage, tags)

    @staticmethod
    def outputRepo(storage, mapper=None, mapperArgs=None, tags=None, mode=None):
        return RepositoryArgs(storage, mapper, mapperArgs, tags, mode)

    def tag(self, tag):
        """add a tag to the repository cfg"""
        if isinstance(tag, basestring):
            self.tags.add(tag)
        else:
            try:
                self.tags.update(tag)
            except TypeError:
                self.tags.add(tag)


class Repository(object):
    """Represents a repository of persisted data and has methods to access that data.
    """

    def __init__(self, repositoryCfg):
        '''Initialize a Repository with parameters input via config.

        :param args: an instance of RepositoryArgs
        :return:
        '''
        self._storage = Storage.makeFromURI(repositoryCfg.root)
        self._mapperArgs = repositoryCfg.mapperArgs # keep for reference in matchesArgs
        self._initMapper(repositoryCfg)

    def _initMapper(self, repositoryCfg):
        '''Initialize and keep the mapper in a member var.

        :param repositoryCfg:
        :return:
        '''

        # rule: If mapper is:
        # - an object: use it as the mapper.
        # - a string: import it and instantiate it with mapperArgs
        # - a class object: instantiate it with mapperArgs
        mapper = repositoryCfg.mapper

        # if mapper is a string, import it:
        if isinstance(mapper, basestring):
            mapper = __import__(mapper)
        # now if mapper is a class type (not instance), instantiate it:
        if inspect.isclass(mapper):
            mapperArgs = copy.copy(repositoryCfg.mapperArgs)
            if mapperArgs is None:
                mapperArgs = {}
            # so that root doesn't have to be redundantly passed in cfgs, if root is specified in the
            # storage and if it is an argument to the mapper, make sure that it's present in mapperArgs.
            for arg in ('root', 'storage'):
                if arg not in mapperArgs:
                    mro = inspect.getmro(mapper)
                    if mro[-1] is object:
                        mro = mro[:-1]
                    for c in mro:
                        if arg in inspect.getargspec(c.__init__ ).args:
                            mapperArgs[arg] = self._storage.root
                            break
            mapper = mapper(**mapperArgs)

        self._mapper = mapper


        def __repr__(self):
            return 'config(id=%s, storage=%s, parent=%s, mapper=%s, mapperArgs=%s, cls=%s)' % \
                   (self.id, self._storage, self.parent, self.mapper, self.mapperArgs, self.cls)


    # todo want a way to make a repository read-only
    def write(self, butlerLocation, obj):
        """Write a dataset to Storage.

        :param butlerLocation: Contains the details needed to find the desired dataset.
        :param dataset: The dataset to be written.
        :return:
        """
        return self._storage.write(butlerLocation, obj)

    def read(self, butlerLocation):
        """Read a dataset from Storage.

        :param butlerLocation: Contains the details needed to find the desired dataset.
        :return: An instance of the dataset requested by butlerLocation.
        """
        return self._storage.read(butlerLocation)

    ###################
    ## Mapper Access ##

    def mappers(self):
        return (self._mapper, )

    def getKeys(self, *args, **kwargs):
        """
        Get the keys available in the repository/repositories.
        :param args:
        :param kwargs:
        :return: A dict of {key:valueType}
        """
        # todo: getKeys is not in the mapper API
        if self._mapper is None:
            return None
        keys = self._mapper.getKeys(*args, **kwargs)
        return keys

    def map(self, *args, **kwargs):
        """Find a butler location for the given arguments.
        See mapper.map for more information about args and kwargs.

        :param args: arguments to be passed on to mapper.map
        :param kwargs: keyword arguments to be passed on to mapper.map
        :return: The type of item is dependent on the mapper being used but is typically a ButlerLocation.
        """
        if self._mapper is None:
            return None
        loc = self._mapper.map(*args, **kwargs)
        if loc is None:
            return None
        loc.setRepository(self)
        return loc

    def queryMetadata(self, *args, **kwargs):
        """Gets possible values for keys given a partial data id.

        See mapper documentation for more explanation about queryMetadata.

        :param args: arguments to be passed on to mapper.queryMetadata
        :param kwargs: keyword arguments to be passed on to mapper.queryMetadata
        :return:The type of item is dependent on the mapper being used but is typically a set that contains
        available values for the keys in the format input argument.
        """
        if self._mapper is None:
            return None
        ret = self._mapper.queryMetadata(*args, **kwargs)
        return ret

    def backup(self, *args, **kwargs):
        """Perform mapper.backup.

        See mapper.backup for more information about args and kwargs.

        :param args: arguments to be passed on to mapper.backup
        :param kwargs: keyword arguments to be passed on to mapper.backup
        :return: None
        """
        if self._mapper is None:
            return None
        self._mapper.backup(*args, **kwargs)

    def getMapperDefaultLevel(self):
        """Get the default level of the mapper.

        This is typically used if no level is passed into butler methods that call repository.getKeys and/or
        repository.queryMetadata. There is a bug in that code because it gets the default level from this
        repository but then uses that value when searching all repositories. If this and other repositories
        have dissimilar data, the default level value will be nonsensical. A good example of this issue is in
        Butler.subset; it needs refactoring.

        :return:
        """
        if self._mapper is None:
            return None
        return self._mapper.getDefaultLevel()


