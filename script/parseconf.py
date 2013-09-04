#! /usr/bin/env python

class ParseConfError(Exception):
    pass

class NameNotExisted(ParseConfError):
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return '<Name Not Existed: %s>' % self.name

class DuplicatedSectionName(ParseConfError):
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return '<Duplicated Section Name: %s>' % self.name

class SectionNotExisted(ParseConfError):
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return '<Section Not Existed: %s>' % self.name

class ParseConf(object):
    '''Parse configuration file.'''
    def __init__(self, file):
        self.globalNVs = {}
        self.sections = {}

        curSection = None
        # read server.conf
        f = open(file, 'r')
        line = f.readline()
        while line != '':
            line = line.strip()
            if line != '' and not line.startswith('#'):
                # data line
                if line.startswith('['):
                    # new section
                    section = line[1:]
                    section = section.rpartition(']')[0]
                    section = section.strip()
                    if self.sections.has_key(section):
                        raise DuplicatedSectionName(section)
                    self.sections[section] = {}
                    curSection = section
                else:
                    (n, _, v) = line.partition('=')
                    n = n.strip()
                    v = v.strip()
                    if not curSection:
                        self.globalNVs[n] = v
                    else:
                        self.sections[curSection][n] = v
            # next line
            line = f.readline()
        f.close()

    def getValue(self, name, section=None):
        if not section:
            # global NV
            if not self.globalNVs.has_key(name):
                raise NameNotExisted(name)
            return self.globalNVs[name]
        else:
            # section NV
            if not self.sections.has_key(section):
                raise SectionNotExisted(section)
            if not self.sections[section].has_key(name):
                raise NameNotExisted(name)
            return self.sections[section][name]

    def enumerateSections(self):
        return self.sections.keys()
