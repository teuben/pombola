from haystack import indexes, routers

class HansardRouter(routers.BaseRouter):
    def for_write(self, **hints):
        print "for_write: " + str(hints)
        return None
        # return 'hansard'

    def for_read(self, **hints):
        print "for_read: " + str(hints)
        return None
        # return 'hansard'

    
    


